"""The context packet an indexing agent reads for one unit of tables.

Everything here is derived from the built map and keygraph.db, so the same inputs give the same packet bytes (no
timestamps). Every free-text field is cut to a bound first and then passed through the secret masking; PII columns
contribute counts and pattern only, never values."""

from __future__ import annotations

import json
import sqlite3

from keygraph.code.refs import mask_secrets
from keygraph.graph import Graph
from keygraph.index.schema import canonical
from keygraph.profile import is_pii_name

DEFAULT_CAP = 6000
SLACK = 40  # tokens kept free for the `trimmed` note added while trimming
# Sections are trimmed in this order (first to last) until the estimate fits; trailing columns go last.
TRIM_ORDER = ("context", "queries", "code_refs", "profiles", "neighbours", "column_details", "candidate_relations")
PII_PROFILE_KEYS = ("sample_size", "null_rate", "distinct_in_sample", "distinct_ratio", "pattern", "length")
PLAIN_PROFILE_KEYS = (*PII_PROFILE_KEYS, "numeric", "dates", "values_complete")
MIN_CONFIDENCE = 0.6
MAX_CODE_REFS, MAX_QUERIES, MAX_NEIGHBOURS, MAX_EVIDENCE, MAX_TOP_VALUES = 5, 3, 8, 3, 8


def estimate(obj) -> int:
    """Token estimate: canonical JSON characters divided by four, rounded up."""
    return (len(canonical(obj)) + 3) // 4


def _txt(v, n: int) -> str:
    """Bound a free-text field before masking (masking can be slow on long tokens), mask it, then cut to n."""
    s = " ".join(str(v if v is not None else "").split())[: n * 2]
    return mask_secrets(s)[:n]


def _rows(ctx, sql: str, args=()) -> list:
    try:
        return ctx.db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # an older keygraph.db without the table
        return []


def _pii_cols(ctx, tid: str, node: dict) -> set[str]:
    cols = {c["name"] for c in node["columns"] if is_pii_name(c["name"])}
    cols |= {r["col"] for r in _rows(ctx, "SELECT col FROM column_semantics WHERE table_id=? AND pii=1", (tid,))}
    return cols


def _profile(raw: str, pii: bool) -> dict:
    try:
        p = json.loads(raw)
    except ValueError:
        return {}
    pii = pii or bool(p.get("pii"))
    out = {k: p[k] for k in (PII_PROFILE_KEYS if pii else PLAIN_PROFILE_KEYS) if k in p}
    if pii:
        out["pii"] = True
    elif p.get("top_values"):
        out["top_values"] = [[_txt(v, 40), c] for v, c in p["top_values"][:MAX_TOP_VALUES]]
    return out


def _table_section(g: Graph, tid: str, ctx) -> dict:
    n = g.nodes[tid]
    pii = _pii_cols(ctx, tid, n)
    cols = []
    for c in n["columns"]:
        d = {"name": c["name"], "type": c["type"], "nullable": c["nullable"]}
        if c.get("key"):
            d["key"] = c["key"]
        if c.get("comment"):
            d["comment"] = _txt(c["comment"], 160)
        if c.get("values"):
            d["values"] = [_txt(v, 40) for v in c["values"][:30]]
        if c["name"] in pii:
            d["pii"] = True
        cols.append(d)
    info = n.get("info") or {}
    return {"table": tid, **({"comment": _txt(info["comment"], 200)} if info.get("comment") else {}),
            **({"rows_estimate": info["rows_estimate"]} if info.get("rows_estimate") is not None else {}),
            "primary_key": n["primary_key"], "unique_keys": [u["columns"] for u in n["unique_keys"]],
            "indexes": [{"name": i["name"], "columns": i["columns"]} for i in n["indexes"] if not i.get("primary")],
            "columns": cols}


def _relations(g: Graph, units: set[str]) -> list[dict]:
    out = []
    for e in g.edges:
        if e.get("rejected") or e["confidence"] < MIN_CONFIDENCE or not ({e["from"], e["to"]} & units):
            continue
        out.append({"from": e["from"], "from_columns": e["from_columns"], "to": e["to"], "to_columns": e["to_columns"],
                    "kind": e["kind"], "confidence": e["confidence"], **({"cardinality": e["cardinality"]} if e.get("cardinality") else {}),
                    "evidence": [_txt(x, 140) for x in (e.get("evidence") or [])[:MAX_EVIDENCE]]})
    return sorted(out, key=lambda r: (-r["confidence"], r["from"], r["from_columns"], r["to"], r["to_columns"]))


def _neighbours(g: Graph, units: set[str]) -> list[dict]:
    count: dict[str, int] = {}
    for e in g.edges:
        if e.get("rejected") or e["confidence"] < MIN_CONFIDENCE:
            continue
        for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
            if a in units and b not in units:
                count[b] = count.get(b, 0) + 1
    top = sorted(count, key=lambda t: (-count[t], t))[:MAX_NEIGHBOURS]
    return [{"table": t, "edges": count[t], **({"comment": _txt(g.nodes[t]["info"]["comment"], 100)}
                                                  if (g.nodes[t].get("info") or {}).get("comment") else {})} for t in top]


def _profiles(g: Graph, ctx, units: list[str]) -> dict:
    out = {}
    for tid in units:
        pii = _pii_cols(ctx, tid, g.nodes[tid])
        for r in _rows(ctx, "SELECT col, profile FROM column_profile WHERE table_id=? ORDER BY col", (tid,)):
            p = _profile(r["profile"], r["col"] in pii or is_pii_name(r["col"]))
            if p:
                out[f"{tid}.{r['col']}"] = p
    return out


def _code_refs(ctx, units: list[str]) -> list[dict]:
    q = ",".join("?" * len(units))
    rows = _rows(ctx, f"SELECT repo, path, line, kind, table_id, column_name, symbol, snippet, confidence FROM code_refs "
                      f"WHERE table_id IN ({q}) ORDER BY confidence DESC, repo, path, line, column_name LIMIT ?",
                 (*units, MAX_CODE_REFS))
    return [{"table": r["table_id"], "column": r["column_name"] or "", "path": _txt(f"{r['repo']}/{r['path']}", 120),
             "line": r["line"], "kind": r["kind"], "symbol": _txt(r["symbol"], 60), "snippet": _txt(r["snippet"], 160)}
            for r in rows]


def _queries(ctx, units: list[str]) -> list[dict]:
    q = ",".join("?" * len(units))
    rows = _rows(ctx, f"SELECT fp, normalized_sql, runs, people FROM query_fingerprints WHERE fp IN "
                      f"(SELECT fp FROM query_refs WHERE table_id IN ({q})) ORDER BY runs DESC, fp LIMIT ?", (*units, MAX_QUERIES))
    return [{"sql": _txt(r["normalized_sql"], 300), "runs": r["runs"], "people": r["people"]} for r in rows]


def _context(ctx, units: list[str]) -> list[dict]:
    q, out = ",".join("?" * len(units)), []
    for r in _rows(ctx, f"SELECT l.concept, l.table_id, c.definition FROM concept_links l LEFT JOIN concepts c ON c.name=l.concept "
                        f"WHERE l.table_id IN ({q}) ORDER BY l.table_id, l.concept", tuple(units)):
        out.append({"table": r["table_id"], "concept": _txt(r["concept"], 60), "definition": _txt(r["definition"], 200)})
    for r in _rows(ctx, f"SELECT table_id, grain, summary, pitfalls FROM table_notes WHERE table_id IN ({q}) ORDER BY table_id", tuple(units)):
        note = {k: _txt(r[k], 200) for k in ("grain", "summary", "pitfalls") if r[k]}
        if note:
            out.append({"table": r["table_id"], "note": note})
    for r in _rows(ctx, f"SELECT table_id, col, meaning, codes FROM column_semantics WHERE table_id IN ({q}) AND "
                        f"COALESCE(pii,0)=0 ORDER BY table_id, col", tuple(units)):
        out.append({"column": f"{r['table_id']}.{r['col']}", "meaning": _txt(r["meaning"], 160),
                    **({"codes": _txt(r["codes"], 160)} if r["codes"] else {})})
    return out


def _drop_details(p: dict) -> None:
    """Drop column comments and enum members (the table shape stays)."""
    for t in p["tables"]:
        for c in t["columns"]:
            c.pop("comment", None)
            c.pop("values", None)


def _drop_columns(p: dict) -> None:
    """Last resort for very wide tables: drop trailing columns of the widest table that are not in a key or index, one at a time."""
    while estimate(p) > p["cap"] - SLACK:
        widest = max(p["tables"], key=lambda t: (len(t["columns"]), t["table"]))
        keep = set(widest["primary_key"]) | {c for u in widest["unique_keys"] for c in u} | {c for i in widest["indexes"] for c in i["columns"]}
        idx = [i for i, c in enumerate(widest["columns"]) if not c.get("key") and c["name"] not in keep]
        if not idx:
            return
        widest["columns"].pop(idx[-1])
        widest["columns_omitted"] = widest.get("columns_omitted", 0) + 1
        if "columns" not in p["trimmed"]:
            p["trimmed"].append("columns")


def build_packet(g: Graph, ctx, tables: list[str], cap: int = DEFAULT_CAP) -> dict:
    """The packet for a unit: tables (columns, keys, indexes), candidate relations with evidence, neighbours, masked
    profiles, top code refs, masked logged queries and context snippets. Trimmed in TRIM_ORDER until estimate() <= cap;
    `trimmed` lists the sections that lost items."""
    units = sorted(tables)
    p: dict = {"unit_tables": units, "tables": [_table_section(g, t, ctx) for t in units],
               "candidate_relations": _relations(g, set(units)), "neighbours": _neighbours(g, set(units)),
               "profiles": _profiles(g, ctx, units), "code_refs": _code_refs(ctx, units), "queries": _queries(ctx, units),
               "context": _context(ctx, units), "trimmed": [], "cap": int(cap)}
    for sec in TRIM_ORDER:
        if estimate(p) <= cap - SLACK:
            break
        if sec == "column_details":
            _drop_details(p)
            p["trimmed"].append(sec)
            continue
        before = len(p[sec])
        while estimate(p) > cap - SLACK and p[sec]:
            p[sec].pop(max(p[sec])) if isinstance(p[sec], dict) else p[sec].pop()
        if len(p[sec]) != before:
            p["trimmed"].append(sec)
    _drop_columns(p)
    return p
