"""Deterministic ranking of tables and grouping into units of 1-5 related tables."""

from __future__ import annotations

import hashlib
import sqlite3

from keygraph.graph import Graph

WEIGHTS = {"hub": 3.0, "queries": 2.0, "code": 1.5, "context": 1.0, "area": 1.0}
MAX_TABLES = 5


def _counts(ctx, sql: str) -> dict[str, int]:
    """table_id -> count from a read-only query; a missing table (older file) counts as empty."""
    try:
        return {r[0]: int(r[1]) for r in ctx.db.execute(sql)}
    except sqlite3.OperationalError:
        return {}


def _norm(raw: dict[str, float], ids: list[str]) -> dict[str, float]:
    top = max((raw.get(t, 0) for t in ids), default=0)
    return {t: (raw.get(t, 0) / top if top else 0.0) for t in ids}


def _real_edges(g: Graph) -> list[dict]:
    mirrors = {t for t, n in g.nodes.items() if n.get("mirror_of")}
    return [e for e in g.edges if e["from"] != e["to"] and e["from"] not in mirrors and e["to"] not in mirrors
            and not e.get("rejected")]


def rank_tables(g: Graph, ctx) -> list[dict]:
    """Rank every table: hub score (incoming relations), query-log hits, code refs, context mentions, area centrality.
    Returns [{"table", "score", "parts"}] best first; ties broken by table id. Same inputs give the same list."""
    ids = sorted(g.nodes)
    incoming: dict[str, int] = {}
    for e in _real_edges(g):
        incoming[e["to"]] = incoming.get(e["to"], 0) + 1
    qhits = _counts(ctx, "SELECT table_id, COUNT(DISTINCT fp) FROM query_refs GROUP BY table_id")
    code = _counts(ctx, "SELECT table_id, COUNT(*) FROM code_refs GROUP BY table_id")
    mentions: dict[str, int] = {}
    for sql in ("SELECT table_id, COUNT(*) FROM concept_links GROUP BY table_id",
                "SELECT table_id, COUNT(*) FROM table_notes WHERE COALESCE(source,'')!='llm' GROUP BY table_id",
                "SELECT table_id, COUNT(*) FROM column_semantics WHERE COALESCE(source,'')!='llm' GROUP BY table_id"):
        for t, n in _counts(ctx, sql).items():
            mentions[t] = mentions.get(t, 0) + n
    area_of = {t: a for a in g.data.get("areas", []) for t in a["tables"]}
    inside: dict[str, int] = {}
    for e in _real_edges(g):
        if e.get("hub") or e.get("role"):
            continue
        if area_of.get(e["from"]) and area_of.get(e["from"]) == area_of.get(e["to"]):
            for t in (e["from"], e["to"]):
                inside[t] = inside.get(t, 0) + 1
    comps = {"hub": _norm(incoming, ids), "queries": _norm(qhits, ids), "code": _norm(code, ids),
             "context": _norm(mentions, ids), "area": _norm(inside, ids)}
    rows = []
    for t in ids:
        parts = {k: round(comps[k][t], 6) for k in WEIGHTS}
        rows.append({"table": t, "score": round(sum(WEIGHTS[k] * parts[k] for k in WEIGHTS), 6), "parts": parts})
    return sorted(rows, key=lambda r: (-r["score"], r["table"]))


def unit_id(tables) -> str:
    """Stable id of a unit: a short hash of its sorted table ids."""
    return hashlib.sha256("\n".join(sorted(tables)).encode("utf-8")).hexdigest()[:12]


def make_units(g: Graph, ranked: list[dict], max_tables: int = MAX_TABLES) -> list[dict]:
    """Group ranked tables into units of 1..max_tables related tables (max 5). Every ranked table lands in exactly one
    unit. A unit grows from its best unassigned table along non-hub, non-role edges to other ranked tables.
    Returns [{"unit_id", "tables" (sorted), "rank" (0-based, by best member), "score"}] in rank order."""
    cap = max(1, min(int(max_tables), MAX_TABLES))
    order = [r["table"] for r in ranked]
    pos = {t: i for i, t in enumerate(order)}
    score = {r["table"]: r["score"] for r in ranked}
    nbrs: dict[str, set[str]] = {t: set() for t in order}
    for e in _real_edges(g):
        if e.get("hub") or e.get("role") or e["from"] not in pos or e["to"] not in pos:
            continue
        nbrs[e["from"]].add(e["to"])
        nbrs[e["to"]].add(e["from"])
    taken: set[str] = set()
    units = []
    for seed in order:
        if seed in taken:
            continue
        members = [seed]
        taken.add(seed)
        while len(members) < cap:
            cand = sorted({n for m in members for n in nbrs[m]} - taken, key=lambda t: pos[t])
            if not cand:
                break
            members.append(cand[0])
            taken.add(cand[0])
        tables = sorted(members)
        units.append({"unit_id": unit_id(tables), "tables": tables, "rank": len(units),
                      "score": round(max(score[t] for t in tables), 6)})
    return units
