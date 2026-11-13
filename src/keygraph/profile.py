"""Value profiling from samples: statistics, top values, patterns, PII masking and enum reconciliation.

PII rule (hard): a column flagged PII (by name, by detected value pattern, or by a context flag) never has raw
values stored. Only counts, null rate, length statistics and the detected pattern name are kept; top values,
minimum and maximum are dropped. Profiles of non-PII columns may hold top values.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
from collections import Counter
from decimal import Decimal

from . import devlog
from .model import type_family

PII_NAME = re.compile(
    r"(^|_)(e?mail|mobile|phone|tel|cell|msisdn|national_?(id|code)|nid|ssn|passport|first_?name|last_?name|full_?name|"
    r"fname|lname|surname|address|addr|postal_?code|zip|token|password|passwd|secret|iban|sheba|card_?(no|number)|"
    r"account_?(no|number)|birth(day|_?date)?|dob|ip_?address|otp|api_?key)($|_)", re.I)
PATTERNS = [
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")),
    ("uuid", re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")),
    ("url", re.compile(r"^https?://\S+$", re.I)),
    ("phone", re.compile(r"^(\+|00)?[0-9]{10,14}$|^09[0-9]{9}$")),
    ("json", re.compile(r"^\s*[\[{].*[\]}]\s*$", re.S)),
    ("persian_text", re.compile(r"[؀-ۿ]")),
]
PII_PATTERNS = {"email", "phone"}
MONEY_NAME = re.compile(r"(price|amount|cost|fee|rial|toman|total|balance|commission|discount|revenue)", re.I)


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def _pct(sorted_vals: list[float], p: float) -> float:
    i = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def _date(v):
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v):
        try:
            return dt.datetime.fromisoformat(v[:19].replace("T", " "))
        except ValueError:
            return None
    return None


def is_pii_name(name: str) -> bool:
    return bool(PII_NAME.search(name or ""))


def profile_values(values: list, name: str, type_str: str = "", pii: bool = False, method: str = "sample",
                   top_k: int = 30, now: dt.datetime | None = None) -> dict:
    """Profile one column from a list of sampled values (None = NULL). Pure function; no I/O."""
    now = now or dt.datetime.now()
    n = len(values)
    nn = [v for v in values if v is not None]
    prof: dict = {"sample_size": n, "null_rate": round(1 - len(nn) / n, 4) if n else None, "method": method,
                  "profiled_at": now.strftime("%Y-%m-%dT%H:%M:%S")}
    if not nn:
        return prof
    texts = [v if isinstance(v, str) else None for v in nn]
    strs = [t for t in texts if t is not None]
    pattern = None
    if strs:
        for pname, rx in PATTERNS:
            hits = sum(1 for t in strs[:500] if rx.search(t))
            if hits >= 0.5 * min(len(strs), 500):
                pattern = pname
                break
    pii = pii or is_pii_name(name) or pattern in PII_PATTERNS
    if pii:
        prof["pii"] = True
    cnt = Counter(v if not isinstance(v, (dict, list, bytes, bytearray)) else json.dumps(v, default=str, sort_keys=True)
                  if not isinstance(v, (bytes, bytearray)) else "<bytes>" for v in nn)
    prof["distinct_in_sample"] = len(cnt)
    prof["distinct_ratio"] = round(len(cnt) / len(nn), 4)
    fam = type_family(type_str)
    nums = [x for x in (_num(v) for v in nn) if x is not None]
    if pattern:
        prof["pattern"] = pattern
    elif nums and MONEY_NAME.search(name or "") and fam in ("int", "decimal", "float", ""):
        prof["pattern"] = "monetary"
    if strs:
        lens = [len(t) for t in strs]
        prof["length"] = {"min": min(lens), "avg": round(sum(lens) / len(lens), 1), "max": max(lens)}
    if len(nums) >= max(1, len(nn) // 2) and not pii:
        s = sorted(nums)
        prof["numeric"] = {"min": s[0], "max": s[-1], "p5": _pct(s, .05), "p25": _pct(s, .25), "p50": _pct(s, .5),
                           "p75": _pct(s, .75), "p95": _pct(s, .95), "mean": round(sum(s) / len(s), 4)}
    dates = [d for d in (_date(v) for v in nn) if d is not None]
    if len(dates) >= max(1, len(nn) // 2):
        prof["dates"] = {"min": min(dates).isoformat(), "max": max(dates).isoformat(),
                         "recency_days": round((now - max(dates)).total_seconds() / 86400, 1)}
    if pii:
        prof["note"] = "PII: raw values withheld; counts and pattern only"
        for k in ("numeric",):
            prof.pop(k, None)
    elif len(cnt) <= 100 or prof["distinct_ratio"] < 0.2:
        prof["top_values"] = [[str(k)[:80], c] for k, c in cnt.most_common(top_k)]
        prof["values_complete"] = len(cnt) <= top_k
        if strs and "length" in prof and prof["length"]["max"] > 80:
            prof["top_values"] = [[k[:80], c] for k, c in prof["top_values"]]
    return prof


def reconcile(prof: dict, declared: list[str] | None, codes: dict | None, observed: set[str] | None = None) -> dict:
    """Compare observed values with declared enum members and context codes. Needs complete observed values."""
    out: dict = {}
    if prof.get("pii") or not prof.get("values_complete") and observed is None:
        return out
    seen = observed if observed is not None else {k for k, _ in prof.get("top_values", [])}
    documented = {str(k) for k in (codes or {})} | {m.split("=")[0] for m in (declared or [])}
    if not documented:
        return out
    out["undocumented_codes"] = sorted(seen - documented)
    out["unseen_codes"] = sorted(documented - seen)
    return {k: v for k, v in out.items() if v}


def summarize(p: dict) -> str:
    bits = [f"{p.get('sample_size', 0)} rows sampled ({p.get('method', '?')}, {p.get('profiled_at', '')[:10]})",
            f"null {p['null_rate']:.0%}" if p.get("null_rate") is not None else ""]
    if "distinct_in_sample" in p:
        bits.append(f"{p['distinct_in_sample']} distinct")
    if p.get("pii"):
        bits.append("PII, values withheld")
    if p.get("pattern"):
        bits.append(f"looks like {p['pattern']}")
    if p.get("numeric"):
        n = p["numeric"]
        bits.append(f"range {n['min']:g}..{n['max']:g}, median {n['p50']:g}")
    if p.get("dates"):
        bits.append(f"dates {p['dates']['min'][:10]}..{p['dates']['max'][:10]} (latest {p['dates']['recency_days']} d ago)")
    if p.get("top_values"):
        bits.append("top: " + ", ".join(f"{k} ({c})" for k, c in p["top_values"][:6]))
    r = p.get("reconcile") or {}
    if r.get("undocumented_codes"):
        bits.append("UNDOCUMENTED in data: " + ", ".join(r["undocumented_codes"][:8]))
    if r.get("unseen_codes"):
        bits.append("documented but not seen: " + ", ".join(r["unseen_codes"][:8]))
    return "; ".join(b for b in bits if b)


def build_sample_sql(dialect: str, table_ref: str, cols: list[str], n: int, strategy: str = "limit", pk: str | None = None,
                     quote: str = '"') -> str:
    q = lambda c: f"{quote}{c}{quote}"  # noqa: E731
    sel = ", ".join(q(c) for c in cols)
    if strategy == "recent" and pk:
        where = f" WHERE {q(pk)} > (SELECT MAX({q(pk)}) FROM {table_ref}) - {2 * n}"
    elif strategy == "tablesample":
        if dialect == "postgres":
            return f"SELECT {sel} FROM {table_ref} TABLESAMPLE SYSTEM (1) LIMIT {n}"
        if dialect == "clickhouse":
            return f"SELECT {sel} FROM {table_ref} SAMPLE 0.01 LIMIT {n}"
        where = ""
    else:
        where = ""
    if dialect == "sqlserver":
        return f"SELECT TOP {n} {sel} FROM {table_ref}{where}"
    return f"SELECT {sel} FROM {table_ref}{where} LIMIT {n}"


def profile_table(source, table, node: dict, n: int = 10000, strategy: str = "auto", budget_s: float = 20.0,
                  skip_over_rows: int | None = 50_000_000, force: bool = False, pii_cols: set[str] | None = None,
                  codes_by_col: dict | None = None, max_cols: int = 200,
                  now: dt.datetime | None = None) -> dict:
    """Profile one table through source.sample(table, cols, n, strategy) -> (cols, rows). Returns {col: profile}
    plus "_meta". Never scans a whole table: the sample is always LIMIT/TOP bounded."""
    rows_est = (node.get("info") or {}).get("rows_estimate")
    if skip_over_rows and rows_est and rows_est > skip_over_rows and not force:
        return {"_meta": {"skipped": f"about {rows_est} rows exceeds the {skip_over_rows} limit (use force)"}}
    cols = [c["name"] for c in node["columns"]][:max_cols]
    pk = node["primary_key"][0] if len(node["primary_key"]) == 1 else None
    pkfam = next((c["family"] for c in node["columns"] if c["name"] == pk), "")
    strat = ("recent" if pk and pkfam == "int" else "limit") if strategy == "auto" else strategy
    t0 = time.time()
    got_cols, rows = source.sample(table, cols, n, strat, pk)
    elapsed = time.time() - t0
    out: dict = {"_meta": {"elapsed_s": round(elapsed, 2), "rows": len(rows), "strategy": strat,
                           "over_budget": elapsed > budget_s}}
    method = f"{strat} n={n}"
    by = {c["name"]: c for c in node["columns"]}
    for i, c in enumerate(got_cols):
        vals = [r[i] for r in rows]
        p = profile_values(vals, c, by[c]["type"], pii=bool(pii_cols and c in pii_cols), method=method, now=now)
        if by[c].get("values") or (codes_by_col or {}).get(c):
            rec = reconcile(p, by[c].get("values"), (codes_by_col or {}).get(c))
            if rec:
                p["reconcile"] = rec
        out[c] = p
    return out


def run_profile(graph, source, ctx=None, tables: list[str] | None = None, top_hubs: int = 0, with_context: bool = False,
                total_budget_s: float = 600.0, log=print, **opts) -> dict:
    """Profile a bounded set of tables; merge into graph.data['profiles'] and (when given) the context db.

    The table set is: explicit `tables`, plus the `top_hubs` most-connected tables, plus every table with context notes."""
    chosen: list[str] = [graph.resolve(t) for t in (tables or [])]
    if top_hubs:
        deg: dict[str, int] = {}
        for e in graph.edges:
            deg[e["from"]] = deg.get(e["from"], 0) + 1
            deg[e["to"]] = deg.get(e["to"], 0) + 1
        chosen += [t for t, _ in sorted(deg.items(), key=lambda x: (-x[1], x[0]))[:top_hubs]]
    pii_cols: dict[str, set] = {}
    codes: dict[str, dict] = {}
    if ctx is not None:
        if with_context:
            chosen += [r["table_id"] for r in ctx.rows("table_notes") if r["table_id"] in graph.nodes]
        for r in ctx.rows("column_semantics"):
            if r["pii"]:
                pii_cols.setdefault(r["table_id"], set()).add(r["col"])
            if r["codes"]:
                codes.setdefault(r["table_id"], {})[r["col"]] = json.loads(r["codes"])
    chosen = list(dict.fromkeys(chosen))
    store = graph.data.setdefault("profiles", {})
    report = {"requested": len(chosen), "profiled": 0, "columns": 0, "skipped": {}, "failed": {}, "findings": [], "seconds": 0}
    t0 = time.time()
    for tid in chosen:
        if time.time() - t0 > total_budget_s:
            report["skipped"][tid] = "total time budget used up"
            devlog.trace("profile.table", table=tid, outcome="skipped", reason="total time budget used up")
            continue
        node = graph.nodes[tid]
        try:
            res = profile_table(source, tid, node, pii_cols=pii_cols.get(tid), codes_by_col=codes.get(tid), **opts)
        except Exception as exc:  # noqa: BLE001
            report["failed"][tid] = str(exc)[:200]
            devlog.trace("profile.table", table=tid, outcome="failed", error_type=type(exc).__name__)  # never the message
            continue
        meta = res.pop("_meta")
        if "skipped" in meta:
            report["skipped"][tid] = meta["skipped"]
            devlog.trace("profile.table", table=tid, outcome="skipped", reason="row estimate over the limit" if "exceeds" in str(meta["skipped"]) else "skipped")
            continue
        for col, p in res.items():
            store[f"{tid}.{col}"] = p
            report["columns"] += 1
            rec = p.get("reconcile") or {}
            if rec.get("undocumented_codes"):
                report["findings"].append(f"{tid}.{col}: values in data but undocumented: {', '.join(rec['undocumented_codes'][:6])}")
        report["profiled"] += 1
        devlog.trace("profile.table", table=tid, outcome="profiled", columns=len(res), rows=meta.get("rows"),
                     ms=round(float(meta.get("elapsed_s") or 0) * 1000, 1), over_budget=bool(meta.get("over_budget")))
        if meta.get("over_budget"):
            report.setdefault("over_budget", []).append(tid)
        log(f"  profiled {tid}: {meta['rows']} rows, {meta['elapsed_s']}s")
    report["seconds"] = round(time.time() - t0, 1)
    devlog.trace("profile.summary", requested=report["requested"], profiled=report["profiled"], columns=report["columns"],
                 skipped=len(report["skipped"]), failed=len(report["failed"]), findings=len(report["findings"]))
    if ctx is not None:
        with devlog.phase("profile.save"):
            ctx.save_profiles(store)
    return report
