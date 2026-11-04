"""Per-table relation summaries grouped by column and area (feeds the Table view and `describe`).

A group is one column (or column set) of this table, or one far-side column for incoming edges.
Its `count` is the number of distinct far tables, and `collapsed` is set when that count exceeds
GROUP_COLLAPSE. `edges` lists every edge id in the group.

A hub label collects this table's outgoing hub or role edges to one target table. Its `count` is
the number of edges to that target (not distinct columns or tables), and `columns` lists the
table's own columns used on those edges.
"""

from __future__ import annotations

from keygraph.areas import area_of
from keygraph.graph import Graph, edge_id

GROUP_COLLAPSE = 8


def relation_summary(graph: Graph, table: str, areas: list[dict], min_conf: float = 0.6,
                     edges: list[dict] | None = None, of: dict[str, str] | None = None) -> dict:
    """edges/of: optional precomputed edges touching the table and table->area map (docs pass them to stay linear)."""
    tid = graph.resolve(table)
    of = of if of is not None else area_of(areas)
    out: dict[tuple, dict] = {}
    inc: dict[tuple, dict] = {}
    hub_labels: dict[str, dict] = {}
    for e in graph.edges if edges is None else edges:
        if e["confidence"] < min_conf:
            continue
        if e["from"] == tid and (e.get("hub") or e.get("role")):
            h = hub_labels.setdefault(e["to"], {"to": e["to"], "columns": [], "count": 0})
            h["columns"] += [c for c in e["from_columns"] if c not in h["columns"]]
            h["count"] += 1
            continue
        if e["from"] == tid:
            key, far, col = ("out", ", ".join(e["from_columns"]), of.get(e["to"])), e["to"], ", ".join(e["from_columns"])
            bucket = out
        elif e["to"] == tid:
            key, far, col = ("in", ", ".join(e["from_columns"]), of.get(e["from"])), e["from"], ", ".join(e["from_columns"])
            bucket = inc
        else:
            continue
        g = bucket.setdefault(key, {"column": col, "area": key[2], "count": 0, "tables": [], "edges": [], "_c": {}})
        g["count"] += 1
        g["_c"][far] = max(g["_c"].get(far, 0), e["confidence"])
        g["edges"].append(edge_id(e))

    def finish(b):
        res = []
        for g in b.values():
            g["tables"] = sorted(g.pop("_c").items(), key=lambda kv: (-kv[1], kv[0]))
            g["tables"] = [t for t, _ in g["tables"]]
            g["count"] = len(g["tables"])
            g["collapsed"] = g["count"] > GROUP_COLLAPSE
            res.append(g)
        return sorted(res, key=lambda g: (-g["count"], g["column"]))

    return {"table": tid, "area": of.get(tid), "out": finish(out), "in": finish(inc),
            "hub_labels": sorted(hub_labels.values(), key=lambda h: (-h["count"], h["to"]))}
