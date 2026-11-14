"""The context packet an indexing agent reads for one unit of tables.

Everything here is derived from the built map and keygraph.db, so the same inputs give the same packet bytes (no
timestamps). Every free-text field is cut to a bound first and then passed through the secret masking; PII columns
contribute counts and pattern only, never values."""

from __future__ import annotations

import json
import sqlite3

from keygraph.code.refs import mask_secrets
from keygraph.graph import Graph
from keygraph.index.pii import looks_pii, mask_keywords, scrub
from keygraph.index.schema import canonical
from keygraph.profile import is_pii_name
from keygraph.queries.fingerprint import mask_sql

DEFAULT_CAP = 6000
SLACK = 40  # tokens kept free for the `trimmed` note added while trimming
# Sections are trimmed in this order (first to last) until the estimate fits; then indexes, then trailing columns.
# Context holds the most trustworthy facts (human notes, earlier answers), so it goes last.
TRIM_ORDER = ("queries", "code_refs", "profiles", "neighbours", "column_details", "candidate_relations", "context")
PII_PROFILE_KEYS = ("sample_size", "null_rate", "distinct_in_sample", "distinct_ratio", "pattern", "length")
PLAIN_PROFILE_KEYS = (*PII_PROFILE_KEYS, "numeric", "dates", "values_complete")
MIN_CONFIDENCE = 0.6
MAX_ENUM_DISTINCT, MAX_ENUM_LEN = 20, 30
MAX_CODE_REFS, MAX_QUERIES, MAX_NEIGHBOURS, MAX_EVIDENCE, MAX_TOP_VALUES = 5, 3, 8, 3, 8


def estimate(obj) -> int:
    """Token estimate: canonical JSON characters divided by four, rounded up."""
    return (len(canonical(obj)) + 3) // 4


def _txt(v, n: int) -> str:
    """Bound a free-text field before masking (masking can be slow on long tokens), mask it, then cut to n."""
    s = " ".join(str(v if v is not None else "").split())[: n * 2]
    return scrub(mask_keywords(mask_secrets(s)))[:n]


def _rows(ctx, sql: str, args=()) -> list:
    try:
        return ctx.db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # an older keygraph.db without the table
        return []


def _pii_cols(ctx, tid: str, node: dict) -> set[str]:
    cols = {c["name"] for c in node["columns"] if is_pii_name(c["name"])}
    cols |= {r["col"] for r in _rows(ctx, "SELECT col FROM column_semantics WHERE table_id=? AND pii=1", (tid,))}
    return cols


def _small_enum(p: dict) -> bool:
    """Top values are safe to send only for a small, complete, short-valued column that holds nothing personal."""
    tv = p.get("top_values") or []
    return bool(tv) and p.get("values_complete") is True and p.get("distinct_in_sample", 99) <= MAX_ENUM_DISTINCT \
        and p.get("pattern") not in ("email", "phone") \
        and all(isinstance(v, str) and len(v) <= MAX_ENUM_LEN and looks_pii(v) is None for v, _ in tv)


def _numeric_safe(p: dict) -> bool:
    """A numeric summary of a non-PII column is dropped when a bound looks like a phone, card or national id."""
    return not any(looks_pii(str(int(v))) for v in (p.get("numeric") or {}).values()
                   if isinstance(v, (int, float)) and abs(v) < 1e19)


def _profile(raw: str, pii: bool) -> dict:
    try:
        p = json.loads(raw)
    except ValueError:
        return {}
    pii = pii or bool(p.get("pii"))
    out = {k: p[k] for k in (PII_PROFILE_KEYS if pii else PLAIN_PROFILE_KEYS) if k in p}
    if pii:
        out["pii"] = True
    else:
        if "numeric" in out and not _numeric_safe(p):
            del out["numeric"]
        if _small_enum(p):
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
        if c["name"] in pii:
            d["pii"] = True  # no comment and no enum members: they could carry real values
        else:
            if c.get("comment"):
                d["comment"] = _txt(c["comment"], 160)
            if c.get("values"):
                d["values"] = [_txt(v, 40) for v in c["values"][:30]]
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
    return [{"sql": _txt(mask_sql(" ".join(str(r["normalized_sql"]).split())[:600], False), 300), "runs": r["runs"], "people": r["people"]} for r in rows]


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


def _note(p: dict, what: str) -> None:
    if what not in p["trimmed"]:
        p["trimmed"].append(what)


def _drop_details(p: dict) -> bool:
    """Drop column comments and enum members (the table shape stays); True when something was dropped."""
    found = False
    for t in p["tables"]:
        for c in t["columns"]:
            found = bool(c.pop("comment", None) is not None) | bool(c.pop("values", None) is not None) | found
    return found


def _group(sec: str, item) -> str:
    """The table an item of a round-robin section belongs to."""
    if sec == "profiles":
        return item.rsplit(".", 1)[0]
    return item.get("table") or item.get("column", "").rsplit(".", 1)[0]


def _pop_one(p: dict, sec: str) -> None:
    """Remove one item. Context and profiles lose items from the table that has the most, so no table is left bare."""
    data = p[sec]
    if sec not in ("context", "profiles"):
        data.pop()
        return
    items = list(data) if sec == "profiles" else data
    count: dict[str, int] = {}
    for it in items:
        count[_group(sec, it)] = count.get(_group(sec, it), 0) + 1
    big = max(count, key=lambda t: (count[t], t))
    if sec == "profiles":
        del data[max(k for k in data if _group(sec, k) == big)]
    else:
        del data[max(i for i, it in enumerate(data) if _group(sec, it) == big)]


def _drop_indexes(p: dict, limit: int) -> None:
    """Drop non-primary indexes, one at a time from the table with the most."""
    while estimate(p) > limit:
        t = max(p["tables"], key=lambda t: (len(t["indexes"]), t["table"]))
        if not t["indexes"]:
            return
        t["indexes"].pop()
        _note(p, "indexes")


def _drop_columns(p: dict, limit: int) -> None:
    """Last resort for very wide tables: drop trailing columns of the widest table that are not in a key or index."""
    while estimate(p) > limit:
        widest = max(p["tables"], key=lambda t: (len(t["columns"]), t["table"]))
        keep = set(widest["primary_key"]) | {c for u in widest["unique_keys"] for c in u} | {c for i in widest["indexes"] for c in i["columns"]}
        idx = [i for i, c in enumerate(widest["columns"]) if not c.get("key") and c["name"] not in keep]
        if not idx:
            return
        widest["columns"].pop(idx[-1])
        widest["columns_omitted"] = widest.get("columns_omitted", 0) + 1
        _note(p, "columns")


def _fit(p: dict, limit: int) -> None:
    for sec in TRIM_ORDER:
        if estimate(p) <= limit:
            return
        if sec == "column_details":
            if _drop_details(p):
                _note(p, sec)
            continue
        while estimate(p) > limit and p[sec]:
            _pop_one(p, sec)
            _note(p, sec)
    _drop_indexes(p, limit)
    _drop_columns(p, limit)


def build_packet(g: Graph, ctx, tables: list[str], cap: int = DEFAULT_CAP) -> dict:
    """The packet for a unit: tables (columns, keys, indexes), candidate relations with evidence, neighbours, masked
    profiles, top code refs, masked logged queries and context snippets. Trimmed in TRIM_ORDER, then indexes, then
    trailing columns, until estimate() <= cap. `trimmed` lists what lost content. When even the required tables
    section is over the cap, the packet says so: `over_cap` is true and `estimate` holds its size (never silent)."""
    units = sorted(tables)
    p: dict = {"unit_tables": units, "tables": [_table_section(g, t, ctx) for t in units],
               "candidate_relations": _relations(g, set(units)), "neighbours": _neighbours(g, set(units)),
               "profiles": _profiles(g, ctx, units), "code_refs": _code_refs(ctx, units), "queries": _queries(ctx, units),
               "context": _context(ctx, units), "trimmed": [], "cap": int(cap)}
    _fit(p, cap - SLACK)
    if estimate(p) > cap:  # the notes added while trimming may have used the slack
        _fit(p, cap)
    if estimate(p) > cap:
        p["over_cap"] = True
        p["estimate"] = estimate(p)
        p["estimate"] = estimate(p)  # the number itself is part of the size
    return p
