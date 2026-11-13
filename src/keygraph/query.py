"""Join-path search, neighbours, column explanation, key listing and text formatting. Reads only the graph."""

from __future__ import annotations

import heapq
import re

from .graph import AmbiguousTable, Graph, UnknownTable
from .mirrors import mirror_tables


def aliases_for(tables: list[str]) -> list[str]:
    used: dict[str, int] = {}
    out = []
    for t in tables:
        name = t.split(".", 1)[1]
        base = "".join(w[0] for w in re.split(r"[_\W]+", name) if w) or "t"
        used[base] = used.get(base, 0) + 1
        out.append(base if used[base] == 1 else f"{base}{used[base]}")
    # a bare single alias that collides with an existing numbered one stays unique by construction
    return out


def join_sql(graph: Graph, path: list[dict]) -> str:
    tables = [path[0]["near"]] + [h["far"] for h in path]
    al = aliases_for(tables)
    lines = [f"FROM {tables[0]} AS {al[0]}"]
    for i, h in enumerate(path):
        on = " AND ".join(f"{al[i]}.{a} = {al[i + 1]}.{b}" for a, b in zip(h["near_cols"], h["far_cols"]))
        lines.append(f"JOIN {tables[i + 1]} AS {al[i + 1]} ON {on}")
    return "\n".join(lines)


HUB_PENALTY, FANOUT_PENALTY, LOG_PENALTY = 0.7, 0.85, 0.8
# log, archive and scratch words: a table named with one of them (as a whole token) is a poor join step
LOG_HINTS = ("log", "logs", "archive", "history", "audit", "bak", "backup", "old", "copy", "test", "shadow", "tmp", "temp")
_LOG_TOKENS = frozenset(LOG_HINTS)
# cardinalities that can multiply rows (a partial composite-key hop is recorded as many-to-many)
FANOUT_CARDS = frozenset({"one-to-many", "many-to-many"})


def _is_log(graph: Graph, t: str) -> bool:
    """True when the table or schema name has a log-like or scratch word as a whole underscore-separated token."""
    n = graph.nodes[t]
    toks = set(re.split(r"[_\W]+", n["table"].lower())) | set(re.split(r"[_\W]+", n["schema"].lower()))
    return bool(toks & _LOG_TOKENS)


def _hop_warnings(graph: Graph, h: dict, end: str | None = None, hubs: set | None = None) -> tuple[list[str], float]:
    """Penalties for one step. Passing *through* a hub costs HUB_PENALTY once (on the step that arrives at it);
    a hub the reader asked for as an endpoint is never penalised."""
    w, f = [], 1.0
    hubs = set(graph.data.get("hubs") or []) if hubs is None else hubs
    if h["far"] in hubs and h["far"] != end:
        w.append("hub"); f *= HUB_PENALTY
    if h["cardinality"] in FANOUT_CARDS:
        w.append("fan_out"); f *= FANOUT_PENALTY
    if _is_log(graph, h["far"]):
        w.append("log"); f *= LOG_PENALTY
    return w, f


def find_paths(graph: Graph, a: str, b: str, max_hops: int = 4, min_conf: float = 0.6, k: int = 3,
               max_pops: int = 300000, include_mirrors: bool = False) -> list[dict]:
    """Best join routes, ranked by score: the product of step confidences times the hub, fan-out and log
    penalties, then fewer hops. Tables in mirror schemas are skipped unless asked for or an endpoint is one."""
    a, b = graph.resolve(a), graph.resolve(b)
    if a == b:
        return []
    adj = graph.adjacency()
    hubs = set(graph.data.get("hubs") or [])
    mirrors = mirror_tables(graph)
    if include_mirrors or a in mirrors or b in mirrors:
        mirrors = set()
    heap: list = [(-1.0, 0, 0, a, ())]
    seq, found, pops = 0, [], 0
    while heap and len(found) < k and pops < max_pops:
        negp, hops, _, node, path = heapq.heappop(heap)
        pops += 1
        if node == b:
            found.append((path, -negp))
            continue
        if hops >= max_hops:
            continue
        seen = {a} | {h["far"] for h in path}
        per_far: dict[str, int] = {}
        for h in sorted(adj.get(node, []), key=lambda x: -x["edge"]["confidence"]):
            c = h["edge"]["confidence"]
            if c < min_conf or h["far"] in seen or (h["far"] in mirrors and h["far"] != b):
                continue
            per_far[h["far"]] = per_far.get(h["far"], 0) + 1
            if per_far[h["far"]] > 2:
                continue
            seq += 1
            warns, factor = _hop_warnings(graph, h, b, hubs)
            heapq.heappush(heap, (negp * c * factor, hops + 1, seq, h["far"], path + (h,)))
    res = []
    raw: list[float] = []
    for path, p in found:
        raw.append(p)
        path = list(path)
        conf = 1.0
        for h in path:
            conf *= h["edge"]["confidence"]
        res.append({
            "tables": [path[0]["near"]] + [h["far"] for h in path], "confidence": round(conf, 3), "score": round(p, 3),
            "hops": len(path),
            "steps": [{"from": h["near"], "from_columns": h["near_cols"], "to": h["far"], "to_columns": h["far_cols"],
                       "kind": h["edge"]["kind"], "status": h["edge"]["status"], "confidence": h["edge"]["confidence"],
                       "cardinality": h["cardinality"], "ambiguous": h["edge"]["ambiguous"], "evidence": h["edge"]["evidence"],
                       "fan_out": h["cardinality"] in FANOUT_CARDS, "warnings": _hop_warnings(graph, h, b, hubs)[0]} for h in path],
            "sql": join_sql(graph, path),
        })
    # sort on the unrounded score (rounding to 3 places would let near-ties fall back to hop count)
    order = sorted(range(len(res)), key=lambda i: (-raw[i], res[i]["hops"], i))
    return [res[i] for i in order]


def format_paths(a: str, b: str, paths: list[dict]) -> str:
    if not paths:
        return f"No join path found between {a} and {b} within the limits (try --max-hops or a lower --min-confidence)."
    out = []
    for i, p in enumerate(paths, 1):
        out.append(f"Path {i}: {' -> '.join(p['tables'])}   (score {p['score']}, confidence {p['confidence']}, {p['hops']} hop{'s' if p['hops'] > 1 else ''})")
        for s in p["steps"]:
            ws = s.get("warnings", [])
            warn = ("  \u26a0 " + " / ".join(w.replace("_", "-") for w in ws)) if ws else ""
            amb = "  [ambiguous]" if s["ambiguous"] else ""
            out.append(f"  {s['from']}({','.join(s['from_columns'])}) -> {s['to']}({','.join(s['to_columns'])})  "
                       f"[{s['kind']} {s['confidence']:.2f}, {s['cardinality']}]{warn}{amb}")
            for ev in s["evidence"]:
                out.append(f"      - {ev}")
        out.append("")
        out.append("  " + p["sql"].replace("\n", "\n  "))
        out.append("")
    return "\n".join(out).rstrip()


def neighbors(graph: Graph, t: str, depth: int = 1, min_conf: float = 0.6) -> dict:
    """Tables related to t. Hops below min_conf are skipped and counted in "hidden" (0 shows everything)."""
    t = graph.resolve(t)
    adj = graph.adjacency()
    seen = {t: 0}
    levels: dict[int, list[dict]] = {}
    frontier = [t]
    hidden: set[str] = set()
    for d in range(1, depth + 1):
        nxt = []
        for node in frontier:
            for h in adj[node]:
                if h["edge"]["confidence"] < min_conf:
                    hidden.add(h["edge"]["id"])
                    continue
                levels.setdefault(d, []).append({
                    "from": h["near"], "from_columns": h["near_cols"], "to": h["far"], "to_columns": h["far_cols"],
                    "kind": h["edge"]["kind"], "confidence": h["edge"]["confidence"], "cardinality": h["cardinality"],
                    "ambiguous": h["edge"]["ambiguous"]})
                if h["far"] not in seen:
                    seen[h["far"]] = d
                    nxt.append(h["far"])
        frontier = nxt
    for lv in levels.values():
        lv.sort(key=lambda x: (-x["confidence"], x["to"]))
    return {"table": t, "depth": depth, "min_confidence": min_conf, "hidden": len(hidden),
            "levels": {str(k): v for k, v in sorted(levels.items())}}


def hidden_note(n: int, floor: float) -> str:
    """The one-line text hint for relations hidden by the confidence floor ("" when none)."""
    return (f"{n} weaker relation{'s' if n != 1 else ''} hidden (below {floor:g}); use --min-confidence 0 to see them"
            if n else "")


def format_neighbors(r: dict) -> str:
    out = [f"{r['table']} (depth {r['depth']})"]
    for d, items in r["levels"].items():
        out.append(f"  hop {d}:")
        for x in items:
            out.append(f"    {x['from']}({','.join(x['from_columns'])}) -> {x['to']}({','.join(x['to_columns'])})  "
                       f"[{x['kind']} {x['confidence']:.2f}, {x['cardinality']}]{' [ambiguous]' if x['ambiguous'] else ''}")
    if len(out) == 1:
        out.append("  no relations found")
    if r.get("hidden"):
        out.append("  " + hidden_note(r["hidden"], r["min_confidence"]))
    return "\n".join(out)


def explain(graph: Graph, ref: str) -> dict:
    t, col = graph.split_column(ref)
    if col is None:
        raise ValueError("explain needs table.column")
    n = graph.nodes[t]
    cd = next(c for c in n["columns"] if c["name"] == col)
    out = {"table": t, "column": col, "type": cd["type"], "detail": column_detail(cd), "column_info": cd,
           "profile": (graph.data.get("profiles") or {}).get(f"{t}.{col}"),
           "in_primary_key": col in n["primary_key"],
           "in_unique_keys": [u["name"] for u in n["unique_keys"] if col in u["columns"]],
           "in_indexes": [{"name": i["name"], "position": i["columns"].index(col) + 1, "columns": i["columns"]}
                          for i in n["indexes"] if col in i["columns"]],
           "references": [], "referenced_by": [], "polymorphic": [p for p in n["polymorphic"] if col in (p["id_column"], p["type_column"])]}
    for e in graph.edges:
        row = {"edge": e["id"], "kind": e["kind"], "status": e["status"], "confidence": e["confidence"],
               "cardinality": e["cardinality"], "ambiguous": e["ambiguous"], "evidence": e["evidence"]}
        if e["from"] == t and col in e["from_columns"]:
            out["references"].append({**row, "table": e["to"], "columns": e["to_columns"]})
        if e["to"] == t and col in e["to_columns"]:
            out["referenced_by"].append({**row, "table": e["from"], "columns": e["from_columns"]})
    out["unresolved"] = col in n["unresolved_id_columns"]
    return out


def format_explain(x: dict) -> str:
    out = [f"{x['table']}.{x['column']}  ({x['detail']})"]
    keys = []
    if x["in_primary_key"]:
        keys.append("primary key")
    keys += [f"unique key {u}" for u in x["in_unique_keys"]]
    keys += [f"index {i['name']} (position {i['position']} of {len(i['columns'])})" for i in x["in_indexes"]]
    out.append("  keys: " + ("; ".join(keys) if keys else "not part of any key or index"))
    if x.get("profile"):
        from .profile import summarize
        out.append("  values: " + summarize(x["profile"]))
    for p in x["polymorphic"]:
        out.append(f"  polymorphic: {p['note']} (id={p['id_column']}, type={p['type_column']})")
    for title, rows in (("references", x["references"]), ("referenced by", x["referenced_by"])):
        if rows:
            out.append(f"  {title}:")
        for r in rows:
            out.append(f"    {r['table']}({','.join(r['columns'])})  [{r['kind']} {r['confidence']:.2f}, {r['cardinality']}]"
                       f"{' [ambiguous]' if r['ambiguous'] else ''}")
            out += [f"        - {e}" for e in r["evidence"]]
    if not (x["references"] or x["referenced_by"] or x["polymorphic"]):
        out.append("  no relations recorded" + (" (looks like an id column but no target table was found)" if x["unresolved"] else ""))
    return "\n".join(out)


def keys(graph: Graph, ref: str) -> dict:
    t = graph.resolve(ref)
    n = graph.nodes[t]
    return {"table": t, "info_line": table_info_line(n), "primary_key": n["primary_key"], "unique_keys": n["unique_keys"], "indexes": n["indexes"],
            "polymorphic": n["polymorphic"], "redundant_indexes": n["redundant_indexes"]}


def format_keys(x: dict) -> str:
    out = [x["table"]]
    if x.get("info_line"):
        out.append("  " + x["info_line"])
    out += ["  primary key: " + (", ".join(x["primary_key"]) or "NONE")]
    out.append("  unique keys: " + ("; ".join(f"{u['name']}({', '.join(u['columns'])})" for u in x["unique_keys"]) or "none"))
    out.append("  indexes (column order matters; filter on the leading column first):")
    for i in x["indexes"]:
        out.append(f"    {i['name']}{' [unique]' if i['unique'] and not i['primary'] else ''}{' [primary]' if i['primary'] else ''}"
                   f"{' [approximate]' if i.get('approximate') else ''}: " + ", ".join(i["columns"]))
    for r in x["redundant_indexes"]:
        out.append(f"  redundant: {r['index']} is covered by {r['covered_by']} ({r['reason']})")
    return "\n".join(out)


def filter_columns(node: dict, ctx_notes: dict | None = None) -> list[str]:
    """Columns that make an efficient WHERE: leading index columns, plus declared date and soft-delete columns."""
    cols: list[str] = []
    for ix in sorted(node["indexes"], key=lambda i: (not i["primary"], i["name"])):
        if ix["columns"] and ix["columns"][0] not in cols:
            cols.append(ix["columns"][0])
    if ctx_notes:
        for k in ("date_column", "soft_delete"):
            v = ctx_notes.get(k)
            if v and v not in cols:
                cols.append(v)
    return cols


def column_detail(c: dict) -> str:
    """One line with everything a table explain shows for a column."""
    bits = [c["type"] + ("" if c.get("nullable", True) else " NOT NULL")]
    if "default" in c:
        bits.append(f"default {c['default']}")
    for k in ("extra", "generated"):
        if c.get(k):
            bits.append(("generated: " if k == "generated" else "") + c[k])
    if c.get("charset") or c.get("collation"):
        bits.append("/".join(x for x in (c.get("charset"), c.get("collation")) if x))
    if c.get("values"):
        bits.append("members: " + ", ".join(c["values"][:30]) + (" ..." if len(c["values"]) > 30 else ""))
    if c.get("comment"):
        bits.append(f"comment: {c['comment']}")
    return "; ".join(bits)


def table_info_line(node: dict) -> str:
    i = node.get("info") or {}
    parts = []
    for k, label in (("engine", "engine"), ("rows_estimate", "rows (estimate)"), ("data_bytes", "data bytes"), ("partitioning", "partitioning"),
                     ("partition_key", "partition key"), ("sorting_key", "order by"), ("comment", "comment")):
        if i.get(k) not in (None, ""):
            parts.append(f"{label}: {i[k]}")
    for ck in i.get("checks", []):
        parts.append(f"check {ck.get('name', '')}: {ck.get('expr', '')}")
    return "; ".join(parts)


def _area_name(areas: list[dict]) -> dict[str, str]:
    return {a["id"]: a["name"] for a in areas}


def list_areas(graph: Graph, include_mirrors: bool = False) -> list[dict]:
    areas = [a for a in graph.data.get("areas", []) if include_mirrors or not a.get("mirror")]
    return [{"id": a["id"], "name": a["name"], "size": a["size"], "tables": list(a["tables"]), "top": a.get("top", []),
             "curated": not a.get("auto", True), **({"mirror": a["mirror"], "mirror_of": a.get("mirror_of")} if a.get("mirror") else {}),
             **({"unconnected": True} if a.get("unconnected") else {})} for a in areas]


def format_areas(rows: list[dict]) -> str:
    if not rows:
        return "no areas"
    return "\n".join(f"{r['id']}  {r['name']}  ({r['size']} tables{', curated' if r['curated'] else ''}): {', '.join(r['top'])}"
                     for r in rows)


def area_detail(graph: Graph, key: str, grains: dict[str, str] | None = None, include_mirrors: bool = False) -> dict:
    """Resolve an area by id, case-insensitive name, or a table (-> its area). Mirror areas need include_mirrors."""
    import difflib

    areas = graph.data.get("areas", [])
    low = key.lower()
    hit = next((a for a in areas if a["id"] == key), None) or next((a for a in areas if a["name"].lower() == low), None)
    if hit is None:
        try:
            t = graph.resolve(key)
        except AmbiguousTable:
            raise
        except UnknownTable:
            names = [a["name"] for a in areas]
            sug = difflib.get_close_matches(key, names, n=3, cutoff=0.5) + difflib.get_close_matches(low, [n.lower() for n in names], n=3, cutoff=0.5)
            sug = [n for n in dict.fromkeys(sug) if n in names] or names[:3]
            raise ValueError(f"unknown area or table '{key}'; areas include: {', '.join(sug)}")
        hit = next((a for a in areas if t in a["tables"]), None)
        if hit is None:
            raise ValueError(f"table {t} is not in any area")
    if hit.get("mirror") and not include_mirrors:
        raise ValueError(f"'{key}' is in schema {hit['mirror']}, a mirror of {hit.get('mirror_of')}; "
                         f"use --include-mirrors to show it, or ask for the original schema's table")
    grains = grains or {}
    inside = set(hit["tables"])
    names = _area_name(areas)
    internal = [{"from": e["from"], "to": e["to"], "columns": e["from_columns"], "confidence": e["confidence"]}
                for e in graph.edges if e["from"] in inside and e["to"] in inside and e["from"] != e["to"]
                and not (e.get("hub") or e.get("role"))]
    nb = []
    for l in graph.data.get("area_links", []):
        if hit["id"] in (l["a"], l["b"]):
            o = l["b"] if l["a"] == hit["id"] else l["a"]
            nb.append({"id": o, "name": names.get(o, o), "edges": l["edges"]})
    nb.sort(key=lambda x: (-x["edges"], x["name"]))
    labels: dict[tuple, dict] = {}
    for e in graph.edges:
        if e["from"] in inside and (e.get("hub") or e.get("role")):
            h = labels.setdefault(e["to"], {"to": e["to"], "columns": [], "count": 0})
            h["count"] += 1
            for c in e["from_columns"]:
                if c not in h["columns"]:
                    h["columns"].append(c)
    return {"id": hit["id"], "name": hit["name"], "curated": not hit.get("auto", True),
            "tables": list(hit["tables"]), "grains": {t: grains[t] for t in hit["tables"] if grains.get(t)},
            "internal": internal, "neighbours": nb, "hub_labels": sorted(labels.values(), key=lambda h: (-h["count"], h["to"]))}


def format_area(d: dict) -> str:
    out = [f"{d['name']} [{d['id']}]{' (curated)' if d['curated'] else ''}: {len(d['tables'])} tables", "", "tables:"]
    out += [f"  {t}" + (f" - {d['grains'][t]}" if t in d["grains"] else "") for t in d["tables"]]
    out += ["", f"internal relations ({len(d['internal'])}):"]
    out += [f"  {r['from']}({', '.join(r['columns'])}) -> {r['to']}  conf {r['confidence']:.2f}" for r in d["internal"][:40]]
    if len(d["internal"]) > 40:
        out.append(f"  ... and {len(d['internal']) - 40} more (use --json)")
    out += ["", "neighbouring areas:"] + ([f"  {n['name']} [{n['id']}]: {n['edges']} relation{'s' if n['edges'] != 1 else ''}" for n in d["neighbours"]] or ["  (none)"])
    out += ["", "hub references:"] + ([f"  {h['to']} via {', '.join(h['columns'])} ({h['count']})" for h in d["hub_labels"]] or ["  (none)"])
    return "\n".join(out)


def format_relation_summary(s: dict) -> str:
    out = ["  relation summary:"]
    for title, key in (("points to", "out"), ("pointed to by", "in")):
        out.append(f"    {title}:")
        for g in s[key]:
            out.append(f"      via {g['column']}: {', '.join(g['tables'][:8])}" + (f" (+{g['count'] - 8} more)" if g["count"] > 8 else ""))
        if not s[key]:
            out.append("      (none)")
    if s["hub_labels"]:
        out.append("    hub references: " + ", ".join(f"{h['to']} via {', '.join(h['columns'])}" for h in s["hub_labels"]))
    return "\n".join(out)
