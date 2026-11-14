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
        try:
            devlog.decide("search.score", hits=len(hits), candidates=len(ranked),
                          tables=[{"table": t, "score": round(score[t], 3), "contributions": contrib[t][:12]} for t in top],
                          cutoff={"limit": limit, "kept": len(top), "last_kept_score": round(score[top[-1]], 3) if top else None,
                                  "first_cut_score": round(score[ranked[limit]], 3) if len(ranked) > limit else None})
        except Exception as exc:  # noqa: BLE001  (a trace point never changes a result)
            devlog._warn(exc)
    out_tables = []
    for t in top:
        c = ctx.table_context(t)
        notes = c["notes"] or {}
        prov_rows = _fact_rows(ctx, t)
        llm_fields = sorted(filled_fields(ctx, t).get("notes", {}))
        node = graph.nodes[t]
        out_tables.append({
            "table": t, "score": round(score[t], 2), "why": why[t][:5], "grain": notes.get("grain", ""),
            "summary": notes.get("summary", ""), "primary_key": node["primary_key"],
            "filter_columns": filter_columns(node, notes), "concepts": sorted({l["concept"] for l in c["concepts"]}),
            "pitfalls": notes.get("pitfalls", ""), "llm_fields": llm_fields,
            "provenance": fact_label(c["notes"]["source"], c["notes"]["verified"], prov_rows.get(("table", t))) if c["notes"] else "",
            "verified": bool(notes.get("verified")) and not llm_fields})
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
        out.append(f"{i}. {t['table']}  (score {t['score']}{', verified' if t['verified'] else ''}"
                   f"{', ' + t['provenance'] if t['provenance'].startswith('llm') else ''})")
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


def fact_label(source, verified, prov: dict | None) -> str:
    """Provenance label of a context fact: llm, "llm, verified by probe" (a probe tested the claim itself),
    "llm, codes seen in data" (the code values occur in the data; their labels are unchecked), verified (human) or
    declared (human, unverified)."""
    if source == "llm":
        if prov and prov.get("verified"):
            return "llm, verified by probe"
        return "llm, codes seen in data" if prov and str(prov.get("verified_by") or "").startswith("seen:") else "llm"
    return "verified" if verified else "declared"


def edge_label(e: dict) -> str:
    """Provenance label of an edge: declared, verified (confirmed or added by a person), llm, or deterministic."""
    if e["kind"] == "declared" or e["status"] == "declared":
        return "declared"
    if e["status"] in ("confirmed", "added"):
        return "verified"
    if e.get("source") == "llm":
        return "llm, verified by probe" if any(x.startswith("llm: probe passed") for x in e["evidence"]) else "llm"
    return "deterministic"


def _fact_rows(ctx: ContextDB, t: str) -> dict:
    """The fact_provenance rows of one table and its columns, keyed (kind, ref)."""
    pre = t + "."
    rows = ctx.rows("fact_provenance", "WHERE ref=? OR (kind='column' AND substr(ref,1,?)=?)", (t, len(pre), pre))
    return {(r["kind"], r["ref"]): r for r in rows}


def filled_fields(ctx: ContextDB, t: str, prov: dict | None = None) -> dict:
    """Fields the indexing agent filled into a row a person wrote: {"notes": {field: {label, verified: false}},
    "columns": {col: {field: ...}}}. The row keeps its human label; only these fields are labelled llm."""
    prov = _fact_rows(ctx, t) if prov is None else prov
    out: dict = {}
    for (kind, ref), r in sorted(prov.items()):
        if kind not in ("table", "column"):
            continue
        try:
            f = (json.loads(r["probe"]) if r["probe"] else {}).get("filled") or {}
        except ValueError:
            continue
        if not f.get("fields"):
            continue
        lab = {"label": fact_label("llm", 0, r), "verified": False}
        flds = {k: dict(lab) for k in sorted(f["fields"])}
        if kind == "table":
            out["notes"] = flds
        else:
            out.setdefault("columns", {})[ref[len(t) + 1:]] = flds
    return out


def _provenance(graph: Graph, ctx: ContextDB, t: str, c: dict, relations: list[dict] | None) -> dict:
    prov = _fact_rows(ctx, t)
    out: dict = {}
    fl = filled_fields(ctx, t, prov)
    if fl:
        out["filled"] = fl
    n = c["notes"]
    if n:
        out["notes"] = fact_label(n["source"], n["verified"], prov.get(("table", t)))
    cols = {x["col"]: fact_label(x["source"], x["verified"], prov.get(("column", f"{t}.{x['col']}"))) for x in c["columns"]}
    if cols:
        out["columns"] = cols
    perf = ctx.rows("performance_notes", "WHERE table_id=?", (t,))
    if perf:
        p = prov.get(("performance", t)) or prov.get(("table", t))
        out["performance"] = fact_label(perf[0]["source"], 0, p)
    if relations is not None:
        by = {}
        for e in graph.edges:
            if t in (e["from"], e["to"]):
                by[(e["from"], tuple(e["from_columns"]), e["to"], tuple(e["to_columns"]))] = e
                by.setdefault((e["to"], tuple(e["to_columns"]), e["from"], tuple(e["from_columns"])), e)
        for x in relations:
            e = by.get((x["from"], tuple(x["from_columns"]), x["to"], tuple(x["to_columns"])))
            if e:
                x["provenance"] = edge_label(e)
    return out


def _performance(ctx: ContextDB, t: str):
    rows = ctx.rows("performance_notes", "WHERE table_id=?", (t,))
    return {**json.loads(rows[0]["notes"] or "{}"), "source": rows[0]["source"]} if rows else None


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
                "performance": _performance(ctx, t), "provenance": _provenance(graph, ctx, t, c, None),
                "columns": [{"name": x["name"], "type": x["type"], **({"key": x["key"]} if x.get("key") else {}),
                             **({"codes": codes[x["name"]]} if x["name"] in codes else {})} for x in graph.nodes[t]["columns"]]}
    from .query import neighbors
    nb = neighbors(graph, t, 1, min_confidence)
    rels = nb["levels"].get("1", [])
    prov = _provenance(graph, ctx, t, c, rels)
    return {"table": t, "keys": keys(graph, t), "performance": _performance(ctx, t), "provenance": prov, "filter_columns": filter_columns(graph.nodes[t], c["notes"]),
            "context": c, "relations": rels,
            "min_confidence": min_confidence, "hidden_relations": nb["hidden"],
            "info_line": table_info_line(graph.nodes[t]),
            "columns": [{**x, "detail": column_detail(x), "profile": (graph.data.get("profiles") or {}).get(f"{t}.{x['name']}")}
                        for x in graph.nodes[t]["columns"]]}


def _perf_lines(d: dict) -> list[str]:
    p = d.get("performance")
    if not p:
        return []
    names = {"default_filters": "default filters", "common_filters": "common filters", "slow_patterns": "slow patterns",
             "index_friendly_filters": "index-friendly filters", "hints": "hints"}
    lab = (d.get("provenance") or {}).get("performance", "")
    out = [f"  performance notes ({lab or p.get('source', '')}):"]
    out += [f"    {names[k]}: " + "; ".join(p[k]) for k in names if p.get(k)]
    return out


_NOTE_FIELDS = ("grain", "summary", "freshness", "soft_delete", "date_column", "pitfalls")


def _llm_note_fields(d: dict) -> set[str]:
    """Table note fields written by the indexing agent: all of them when the whole row is llm, else the fields it filled."""
    n = d["context"]["notes"] or {}
    if n.get("source") == "llm":
        return set(_NOTE_FIELDS)
    return set(((d.get("provenance") or {}).get("filled") or {}).get("notes", {}))


def _llm_col_mark(d: dict, name: str) -> str:
    """` (llm...)` for a column whose stored semantics the indexing agent wrote or filled, else an empty string."""
    prov = d.get("provenance") or {}
    lab = (prov.get("columns") or {}).get(name)
    if lab and lab.startswith("llm"):
        return f" ({lab})"
    f = (prov.get("filled") or {}).get("columns", {}).get(name)
    return " (llm: " + ", ".join(f) + ")" if f else ""


def format_describe(d: dict) -> str:
    from .query import format_keys
    out = [format_keys(d["keys"])]
    if d.get("brief"):
        n = d["context"]["notes"]
        if n:
            llm = _llm_note_fields(d)
            out.append(f"  grain: {n['grain']}" + (" (llm)" if "grain" in llm else ""))
            for k in ("summary", "pitfalls"):
                if n.get(k):
                    out.append(f"  {k}: {n[k]}" + (" (llm)" if k in llm else ""))
        out.append("  efficient filters: " + (", ".join(d["filter_columns"]) or "n/a"))
        out += _perf_lines(d)
        out.append("  columns:")
        for c in d["columns"]:
            codes = " codes: " + ", ".join((f"{k}={v}" if v else str(k)) for k, v in c["codes"].items()) if c.get("codes") else ""
            out.append(f"    {c['name']} {c['type']}{' ' + c['key'] if c.get('key') else ''}{codes}{_llm_col_mark(d, c['name']) if codes else ''}")
        return "\n".join(out)
    n = d["context"]["notes"]
    if n:
        llm = _llm_note_fields(d)
        out.append(f"  grain: {n['grain']}" + (" (llm)" if "grain" in llm else ""))
        for k in ("summary", "freshness", "soft_delete", "date_column", "pitfalls"):
            if n.get(k):
                out.append(f"  {k}: {n[k]}" + (" (llm)" if k in llm else ""))
        out.append(f"  context source: {safe_source(n['source'])}  verified: {bool(n['verified'])}"
                   + (f"  provenance: {d['provenance']['notes']}" if d.get("provenance", {}).get("notes") else ""))
    out.append("  efficient filters: " + (", ".join(d["filter_columns"]) or "n/a"))
    out += _perf_lines(d)
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
            extra += " codes: " + ", ".join((f"{k}={v}" if v else str(k)) for k, v in s["codes"].items())
        if s and s["pii"]:
            extra += " [PII]"
        if s:
            extra += _llm_col_mark(d, c["name"])
        pf = c.get("profile")
        if pf:
            from .profile import summarize
            extra += "  [" + summarize(pf) + "]"
        out.append(f"    {c['name']} {c['detail']}{extra}")
    if d["relations"]:
        out.append("  relations:")
        for x in d["relations"]:
            out.append(f"    {x['from']}({','.join(x['from_columns'])}) -> {x['to']}({','.join(x['to_columns'])}) [{x['kind']} {x['confidence']:.2f}"
                       + (f", {x['provenance']}" if x.get("provenance", "").startswith("llm") else "") + "]")
    if d.get("hidden_relations"):
        from .query import hidden_note
        out.append("  " + hidden_note(d["hidden_relations"], d["min_confidence"]))
    return "\n".join(out)
