"""Join-path search, neighbours, column explanation, key listing and text formatting. Reads only the graph."""

from __future__ import annotations

import heapq
import re

from .graph import Graph


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


def find_paths(graph: Graph, a: str, b: str, max_hops: int = 4, min_conf: float = 0.6, k: int = 3,
               max_pops: int = 300000) -> list[dict]:
    a, b = graph.resolve(a), graph.resolve(b)
    if a == b:
        return []
    adj = graph.adjacency()
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
            if c < min_conf or h["far"] in seen:
                continue
            per_far[h["far"]] = per_far.get(h["far"], 0) + 1
            if per_far[h["far"]] > 2:
                continue
            seq += 1
            heapq.heappush(heap, (negp * c, hops + 1, seq, h["far"], path + (h,)))
    res = []
    for path, p in found:
        path = list(path)
        res.append({
            "tables": [path[0]["near"]] + [h["far"] for h in path], "confidence": round(p, 3), "hops": len(path),
            "steps": [{"from": h["near"], "from_columns": h["near_cols"], "to": h["far"], "to_columns": h["far_cols"],
                       "kind": h["edge"]["kind"], "status": h["edge"]["status"], "confidence": h["edge"]["confidence"],
                       "cardinality": h["cardinality"], "ambiguous": h["edge"]["ambiguous"], "evidence": h["edge"]["evidence"],
                       "fan_out": h["cardinality"] == "one-to-many"} for h in path],
            "sql": join_sql(graph, path),
        })
    return res


def format_paths(a: str, b: str, paths: list[dict]) -> str:
    if not paths:
        return f"No join path found between {a} and {b} within the limits (try --max-hops or a lower --min-confidence)."
    out = []
    for i, p in enumerate(paths, 1):
        out.append(f"Path {i}: {' -> '.join(p['tables'])}   (confidence {p['confidence']}, {p['hops']} hop{'s' if p['hops'] > 1 else ''})")
        for s in p["steps"]:
            warn = "  [fan-out: one row becomes many]" if s["fan_out"] else ""
            amb = "  [ambiguous]" if s["ambiguous"] else ""
            out.append(f"  {s['from']}({','.join(s['from_columns'])}) -> {s['to']}({','.join(s['to_columns'])})  "
                       f"[{s['kind']} {s['confidence']:.2f}, {s['cardinality']}]{warn}{amb}")
            for ev in s["evidence"]:
                out.append(f"      - {ev}")
        out.append("")
        out.append("  " + p["sql"].replace("\n", "\n  "))
        out.append("")
    return "\n".join(out).rstrip()


def neighbors(graph: Graph, t: str, depth: int = 1, min_conf: float = 0.0) -> dict:
    t = graph.resolve(t)
    adj = graph.adjacency()
    seen = {t: 0}
    levels: dict[int, list[dict]] = {}
    frontier = [t]
    for d in range(1, depth + 1):
        nxt = []
        for node in frontier:
            for h in adj[node]:
                if h["edge"]["confidence"] < min_conf:
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
    return {"table": t, "depth": depth, "levels": {str(k): v for k, v in sorted(levels.items())}}


def format_neighbors(r: dict) -> str:
    out = [f"{r['table']} (depth {r['depth']})"]
    for d, items in r["levels"].items():
        out.append(f"  hop {d}:")
        for x in items:
            out.append(f"    {x['from']}({','.join(x['from_columns'])}) -> {x['to']}({','.join(x['to_columns'])})  "
                       f"[{x['kind']} {x['confidence']:.2f}, {x['cardinality']}]{' [ambiguous]' if x['ambiguous'] else ''}")
    if len(out) == 1:
        out.append("  no relations found")
    return "\n".join(out)


def explain(graph: Graph, ref: str) -> dict:
    t, col = graph.split_column(ref)
    if col is None:
        raise ValueError("explain needs table.column")
    n = graph.nodes[t]
    out = {"table": t, "column": col, "type": next(c["type"] for c in n["columns"] if c["name"] == col),
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
    out = [f"{x['table']}.{x['column']}  ({x['type']})"]
    keys = []
    if x["in_primary_key"]:
        keys.append("primary key")
    keys += [f"unique key {u}" for u in x["in_unique_keys"]]
    keys += [f"index {i['name']} (position {i['position']} of {len(i['columns'])})" for i in x["in_indexes"]]
    out.append("  keys: " + ("; ".join(keys) if keys else "not part of any key or index"))
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
    return {"table": t, "primary_key": n["primary_key"], "unique_keys": n["unique_keys"], "indexes": n["indexes"],
            "polymorphic": n["polymorphic"], "redundant_indexes": n["redundant_indexes"]}


def format_keys(x: dict) -> str:
    out = [x["table"], "  primary key: " + (", ".join(x["primary_key"]) or "NONE")]
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
