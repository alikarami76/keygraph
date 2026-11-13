"""Business-question search over names + context, describe, and context-to-graph evidence."""

from __future__ import annotations

import json

from . import devlog
from .context import ContextDB, safe_source
from .graph import Graph
from .query import column_detail, filter_columns, find_paths, table_info_line

WEIGHT = {"concept": 1.0, "table_note": 1.2, "metric": 1.0, "column_ctx": 0.8, "column_value": 0.7, "table": 1.0, "column": 0.5}


def apply_context_to_graph(graph: Graph, ctx: ContextDB) -> int:
    """Concept relations that map onto a graph edge add evidence and raise confidence (idempotent)."""
    prim: dict[str, set[str]] = {}
    for l in ctx.rows("concept_links"):
        if l["role"] == "primary-record":
            prim.setdefault(l["concept"], set()).add(l["table_id"])
    by_id = {e["id"]: e for e in graph.edges}
    pair: dict[tuple[str, str], list[dict]] = {}
    for e in graph.edges:
        pair.setdefault((e["from"], e["to"]), []).append(e)
        pair.setdefault((e["to"], e["from"]), []).append(e)
    n = 0
    for r in ctx.rows("concept_relations"):
        targets = [by_id[r["edge_id"]]] if r["edge_id"] in by_id else [
            e for a in prim.get(r["from_concept"], ()) for b in prim.get(r["to_concept"], ()) for e in pair.get((a, b), [])]
        for e in targets:
            msg = f"business relation: {r['from_concept']} {r['rel']} {r['to_concept']} (source: {safe_source(r['source'])})"
            if msg in e["evidence"]:
                continue
            e["evidence"].append(msg)
            if e["kind"] == "inferred":
                e["confidence"] = round(min(0.95, e["confidence"] + (0.2 if r["verified"] else 0.1)), 2)
            n += 1
    graph.refresh_stats()
    return n


def search(graph: Graph, ctx: ContextDB, query: str, limit: int = 8, with_paths: bool = True) -> dict:
    hits = ctx.fts(query)
    score: dict[str, float] = {}
    why: dict[str, list[str]] = {}
    concepts, metrics = [], []

    dec = devlog.active()  # dev mode: keep each table's score contributions (kind, ref, weight)
    contrib: dict[str, list[dict]] = {}
    src: list = ["", ""]

    def add(tid, s, reason):
        if tid in graph.nodes:
            if dec:
                contrib.setdefault(tid, []).append({"kind": src[0], "ref": src[1], "weight": round(s, 3)})
            score[tid] = score.get(tid, 0) + s
            if reason not in why.setdefault(tid, []):
                why[tid].append(reason)

    links = ctx.rows("concept_links")
    by_concept: dict[str, list[dict]] = {}
    for l in links:
        by_concept.setdefault(l["concept"], []).append(l)
    rels = ctx.rows("concept_relations")
    for kind, ref, s in hits:
        w = WEIGHT.get(kind, 0.5) * s
        src[0], src[1] = kind, ref
        if kind == "table":
            add(ref, w, "table name matches")
        elif kind == "column":
            add(ref.rsplit(".", 1)[0], w, f"column {ref.rsplit('.', 1)[1]} matches")
        elif kind == "column_value":
            add(ref.rsplit(".", 1)[0], w, f"observed values of {ref.rsplit('.', 1)[1]} match")
        elif kind == "column_ctx":
            add(ref.rsplit(".", 1)[0], w, f"column context: {ref.rsplit('.', 1)[1]}")
        elif kind == "table_note":
            add(ref, w, "table notes match")
        elif kind == "concept":
            concepts.append(ref)
            for l in by_concept.get(ref, []):
                add(l["table_id"], w * (1.0 if l["role"] == "primary-record" else 0.5), f"concept '{ref}' ({l['role']})")
            for r in rels:
                other = r["to_concept"] if r["from_concept"] == ref else r["from_concept"] if r["to_concept"] == ref else None
                if other:
                    for l in by_concept.get(other, []):
                        if l["role"] == "primary-record":
                            add(l["table_id"], w * 0.4, f"related concept '{other}' ({r['from_concept']} {r['rel']} {r['to_concept']})")
        elif kind == "metric":
            m = ctx.rows("metrics", "WHERE name=?", (ref,))[0]
            metrics.append(ref)
            for t in json.loads(m["tables"] or "[]"):
                add(t, w, f"metric '{ref}'")
    ranked = sorted(score, key=lambda t: (-score[t], t))
    top = ranked[:limit]
    if dec:
        devlog.decide("search.score", hits=len(hits), candidates=len(ranked),
                      tables=[{"table": t, "score": round(score[t], 3), "contributions": contrib[t][:12]} for t in top],
                      cutoff={"limit": limit, "kept": len(top), "last_kept_score": round(score[top[-1]], 3) if top else None,
                              "first_cut_score": round(score[ranked[limit]], 3) if len(ranked) > limit else None})
    out_tables = []
    for t in top:
        c = ctx.table_context(t)
        notes = c["notes"] or {}
        node = graph.nodes[t]
        out_tables.append({
            "table": t, "score": round(score[t], 2), "why": why[t][:5], "grain": notes.get("grain", ""),
            "summary": notes.get("summary", ""), "primary_key": node["primary_key"],
            "filter_columns": filter_columns(node, notes), "concepts": sorted({l["concept"] for l in c["concepts"]}),
            "pitfalls": notes.get("pitfalls", ""), "verified": bool(notes.get("verified"))})
    paths = []
    if with_paths and len(top) >= 2:
        for other in top[1:3]:
            ps = find_paths(graph, top[0], other, 4, 0.6, 1)
            if ps:
                paths.append({"from": top[0], "to": other, **ps[0]})
    return {"query": query, "tables": out_tables, "concepts": sorted(set(concepts)), "metrics": sorted(set(metrics)),
            "join_paths": paths}


def format_search(r: dict) -> str:
    out = [f"Search: {r['query']}"]
    if r["concepts"]:
        out.append("  concepts: " + ", ".join(r["concepts"]))
    if r["metrics"]:
        out.append("  metrics: " + ", ".join(r["metrics"]))
    if not r["tables"]:
        out.append("  no matching tables")
    for i, t in enumerate(r["tables"], 1):
        out.append(f"{i}. {t['table']}  (score {t['score']}{', verified' if t['verified'] else ''})")
        if t["grain"] or t["summary"]:
            out.append(f"     grain/summary: {t['grain'] or t['summary']}"[:260])
        out.append(f"     pk: {', '.join(t['primary_key']) or 'none'}   filter on: {', '.join(t['filter_columns']) or 'n/a'}")
        out.append("     why: " + "; ".join(t["why"]))
        if t["pitfalls"]:
            out.append(f"     pitfalls: {t['pitfalls']}"[:260])
    for p in r["join_paths"]:
        out.append(f"join {p['from']} .. {p['to']} (confidence {p['confidence']}):")
        out.append("   " + p["sql"].replace("\n", "\n   "))
    return "\n".join(out)


def describe(graph: Graph, ctx: ContextDB, ref: str, brief: bool = False, min_confidence: float = 0.6) -> dict:
    """Everything about one table. brief: keys, filter columns, table notes and columns as name/type/key/codes only
    (no profiles, relations or per-column prose), a cheap first look at a wide table.
    Relations below min_confidence are left out and counted in hidden_relations."""
    from .query import keys
    t = graph.resolve(ref)
    c = ctx.table_context(t)
    if brief:
        codes = {x["col"]: x["codes"] for x in c["columns"] if x.get("codes")}
        return {"table": t, "brief": True, "keys": keys(graph, t), "filter_columns": filter_columns(graph.nodes[t], c["notes"]),
                "context": {"notes": c["notes"]}, "info_line": table_info_line(graph.nodes[t]),
                "columns": [{"name": x["name"], "type": x["type"], **({"key": x["key"]} if x.get("key") else {}),
                             **({"codes": codes[x["name"]]} if x["name"] in codes else {})} for x in graph.nodes[t]["columns"]]}
    from .query import neighbors
    nb = neighbors(graph, t, 1, min_confidence)
    return {"table": t, "keys": keys(graph, t), "filter_columns": filter_columns(graph.nodes[t], c["notes"]),
            "context": c, "relations": nb["levels"].get("1", []),
            "min_confidence": min_confidence, "hidden_relations": nb["hidden"],
            "info_line": table_info_line(graph.nodes[t]),
            "columns": [{**x, "detail": column_detail(x), "profile": (graph.data.get("profiles") or {}).get(f"{t}.{x['name']}")}
                        for x in graph.nodes[t]["columns"]]}


def format_describe(d: dict) -> str:
    from .query import format_keys
    out = [format_keys(d["keys"])]
    if d.get("brief"):
        n = d["context"]["notes"]
        if n:
            out.append(f"  grain: {n['grain']}")
            for k in ("summary", "pitfalls"):
                if n.get(k):
                    out.append(f"  {k}: {n[k]}")
        out.append("  efficient filters: " + (", ".join(d["filter_columns"]) or "n/a"))
        out.append("  columns:")
        for c in d["columns"]:
            codes = " codes: " + ", ".join(f"{k}={v}" for k, v in c["codes"].items()) if c.get("codes") else ""
            out.append(f"    {c['name']} {c['type']}{' ' + c['key'] if c.get('key') else ''}{codes}")
        return "\n".join(out)
    n = d["context"]["notes"]
    if n:
        out.append(f"  grain: {n['grain']}")
        for k in ("summary", "freshness", "soft_delete", "date_column", "pitfalls"):
            if n.get(k):
                out.append(f"  {k}: {n[k]}")
        out.append(f"  context source: {safe_source(n['source'])}  verified: {bool(n['verified'])}")
    out.append("  efficient filters: " + (", ".join(d["filter_columns"]) or "n/a"))
    if d["context"]["concepts"]:
        out.append("  concepts: " + ", ".join(f"{l['concept']} ({l['role']})" for l in d["context"]["concepts"]))
    for m in d["context"]["metrics"]:
        out.append(f"  metric {m['name']}: {m['definition']}  [{m['expression']}]")
    sem = {c["col"]: c for c in d["context"]["columns"]}
    out.append("  columns:")
    for c in d["columns"]:
        s = sem.get(c["name"])
        extra = f" - {s['meaning']}" if s and s["meaning"] else ""
        if s and s["unit"]:
            extra += f" [{s['unit']}]"
        if s and s["codes"]:
            extra += " codes: " + ", ".join(f"{k}={v}" for k, v in s["codes"].items())
        if s and s["pii"]:
            extra += " [PII]"
        pf = c.get("profile")
        if pf:
            from .profile import summarize
            extra += "  [" + summarize(pf) + "]"
        out.append(f"    {c['name']} {c['detail']}{extra}")
    if d["relations"]:
        out.append("  relations:")
        for x in d["relations"]:
            out.append(f"    {x['from']}({','.join(x['from_columns'])}) -> {x['to']}({','.join(x['to_columns'])}) [{x['kind']} {x['confidence']:.2f}]")
    if d.get("hidden_relations"):
        from .query import hidden_note
        out.append("  " + hidden_note(d["hidden_relations"], d["min_confidence"]))
    return "\n".join(out)
