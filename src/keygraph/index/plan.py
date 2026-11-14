"""Deterministic ranking of tables and grouping into units of 1-5 related tables."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3

from keygraph.graph import Graph

# The first five are the strong signals (declared and inferred relations, the query log, code references, documented
# context, area centrality). `rows`, `degree` and `presence` are weak generic warehouse signals: how much data a table
# holds (log-scaled), how many relations touch it, and a penalty for tables known to be empty or tiny. They never
# outweigh a table that people query or code uses.
WEIGHTS = {"hub": 3.0, "queries": 2.0, "code": 1.5, "context": 1.0, "area": 1.0, "rows": 0.6, "degree": 0.4, "presence": 1.0}
MAX_TABLES = 5
TINY_ROWS = 10  # fewer rows than this (but known) ranks lower; zero rows ranks lowest
PRESENCE = {"empty": -1.0, "tiny": -0.5}
MIN_FAMILY_COLUMNS = 3  # a 2-column shape (id, name) is too generic to call two tables copies of each other
MAX_FAMILY_LIST = 50


def _counts(ctx, sql: str) -> dict[str, int]:
    """table_id -> count from a read-only query; a missing table (older file) counts as empty."""
    try:
        return {r[0]: int(r[1]) for r in ctx.db.execute(sql)}
    except sqlite3.OperationalError:
        return {}


def _norm(raw: dict[str, float], ids: list[str]) -> dict[str, float]:
    top = max((raw.get(t, 0) for t in ids), default=0)
    return {t: (raw.get(t, 0) / top if top else 0.0) for t in ids}


def skipped_tables(g: Graph) -> set[str]:
    """Mirror and copy-schema tables: planning leaves them out, they keep the deterministic map."""
    return {t for t, n in g.nodes.items() if n.get("mirror_of") or g._is_mirror_or_copy(n["schema"])}


def _real_edges(g: Graph) -> list[dict]:
    skip = skipped_tables(g)
    return [e for e in g.edges if e["from"] != e["to"] and e["from"] not in skip and e["to"] not in skip
            and not e.get("rejected")]


def _evidence(ctx) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """(query-log hits, code references, context mentions) per table."""
    qhits = _counts(ctx, "SELECT table_id, COUNT(DISTINCT fp) FROM query_refs GROUP BY table_id")
    code = _counts(ctx, "SELECT table_id, COUNT(*) FROM code_refs GROUP BY table_id")
    mentions: dict[str, int] = {}
    for sql in ("SELECT table_id, COUNT(*) FROM concept_links GROUP BY table_id",
                "SELECT table_id, COUNT(*) FROM table_notes WHERE COALESCE(source,'')!='llm' GROUP BY table_id",
                "SELECT table_id, COUNT(*) FROM column_semantics WHERE COALESCE(source,'')!='llm' GROUP BY table_id"):
        for t, n in _counts(ctx, sql).items():
            mentions[t] = mentions.get(t, 0) + n
    return qhits, code, mentions


def _degree(g: Graph) -> dict[str, int]:
    """table_id -> number of distinct related tables (edges in either direction)."""
    nbrs: dict[str, set[str]] = {}
    for e in _real_edges(g):
        nbrs.setdefault(e["from"], set()).add(e["to"])
        nbrs.setdefault(e["to"], set()).add(e["from"])
    return {t: len(n) for t, n in nbrs.items()}


def _rows(g: Graph, ctx) -> dict[str, int]:
    """table_id -> known row count: the adapter's estimate, or the largest profile sample (a lower bound). Unknown tables are absent."""
    out: dict[str, int] = {}
    for t, n in g.nodes.items():
        est = (n.get("info") or {}).get("rows_estimate")
        if isinstance(est, (int, float)) and est >= 0:
            out[t] = int(est)
    for t, n in _counts(ctx, "SELECT table_id, MAX(sample_size) FROM column_profile GROUP BY table_id").items():
        out[t] = max(out.get(t, 0), n)
    return out


def _signature(node: dict) -> tuple:
    """Column names and types, order-insensitive."""
    return tuple(sorted((c["name"].lower(), " ".join(str(c["type"]).lower().split())) for c in node["columns"]))


def _profiles_of(ctx, tid: str) -> dict[str, dict]:
    out = {}
    try:
        for r in ctx.db.execute("SELECT col, profile FROM column_profile WHERE table_id=? ORDER BY col", (tid,)):
            try:
                out[r[0]] = json.loads(r[1])
            except ValueError:
                pass
    except sqlite3.OperationalError:
        pass
    return out


def _range_overlap(a: dict | None, b: dict | None) -> bool | None:
    if not a or "min" not in a or "max" not in a or not b or "min" not in b or "max" not in b:
        return None
    try:
        return a["min"] <= b["max"] and b["min"] <= a["max"]
    except TypeError:
        return None


def _col_overlap(pa: dict, pb: dict) -> bool | None:
    """True/False when two column profiles share/cannot share values, None when they say nothing comparable."""
    verdicts = [_range_overlap(pa.get("numeric"), pb.get("numeric")), _range_overlap(pa.get("dates"), pb.get("dates"))]
    ta, tb = pa.get("top_values"), pb.get("top_values")
    if ta and tb:
        shared = {v for v, _ in ta} & {v for v, _ in tb}
        both_complete = pa.get("values_complete") is True and pb.get("values_complete") is True
        verdicts.append(True if shared else (False if both_complete else None))
    known = [v for v in verdicts if v is not None]
    return None if not known else any(known)


def _same_data(ctx, a: str, b: str, key_cols: list[str], cache: dict) -> bool:
    """False only when profiles of both tables exist and every comparable column (the key columns first) shows the two hold
    different values. Without comparable profiles the shape alone decides (True)."""
    for t in (a, b):
        if t not in cache:
            cache[t] = _profiles_of(ctx, t)
    pa, pb = cache[a], cache[b]
    shared = sorted(set(pa) & set(pb))
    for cols in ([c for c in key_cols if c in shared], shared):
        verdicts = [v for v in (_col_overlap(pa[c], pb[c]) for c in cols) if v is not None]
        if verdicts:
            return any(verdicts)
    return True


def families(g: Graph, ctx) -> dict[str, list[str]]:
    """{representative: [copies]}; see `family_report`."""
    return family_report(g, ctx)[0]


def family_report(g: Graph, ctx) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Near-duplicate families: tables with the same column signature (names and types, any order, at least 3 columns) that,
    when profiles exist, still share key or column values. Returns {representative: [copies]} (copies sorted), only for
    families of two or more, and {representative: what chose it: usage, rows, relations or name}. The representative is the member that people use (most query-log hits, code references and context mentions), then the one with
    the most rows, then the most relations, then the smallest table id, so a table with real usage never becomes a copy.
    Copies get no unit of their own: their only note is "copy of <representative>" (index/families.json and the
    representative's packet). Deterministic: same map and profiles, same families."""
    skip = skipped_tables(g)
    rows, deg = _rows(g, ctx), _degree(g)
    ev = _evidence(ctx)
    strong = {t: sum(d.get(t, 0) for d in ev) for t in g.nodes}
    groups: dict[tuple, list[str]] = {}
    for t in sorted(g.nodes):
        n = g.nodes[t]
        if t not in skip and len(n["columns"]) >= MIN_FAMILY_COLUMNS:
            groups.setdefault(_signature(n), []).append(t)
    cache: dict[str, dict] = {}
    out: dict[str, list[str]] = {}
    basis: dict[str, str] = {}
    for sig, members in sorted(groups.items(), key=lambda kv: kv[1][0]):
        if len(members) < 2:
            continue
        clusters: list[list[str]] = []
        for t in members:  # sorted: each table joins the first cluster whose first member holds the same data
            key_cols = g.nodes[t]["primary_key"]
            for cl in clusters:
                if _same_data(ctx, cl[0], t, key_cols, cache):
                    cl.append(t)
                    break
            else:
                clusters.append([t])
        for cl in clusters:
            if len(cl) > 1:
                keys = {t: (-strong[t], -rows.get(t, -1), -deg.get(t, 0)) for t in cl}
                order = sorted(cl, key=lambda t: (*keys[t], t))
                rep = order[0]
                out[rep] = sorted(set(cl) - {rep})
                first = next((i for i in range(3) if keys[order[0]][i] != keys[order[1]][i]), 3)
                basis[rep] = ("usage", "rows", "relations", "name")[first]
    return dict(sorted(out.items())), dict(sorted(basis.items()))


def copy_of(fams: dict[str, list[str]]) -> dict[str, str]:
    """copy table -> representative."""
    return {c: rep for rep, cs in fams.items() for c in cs}


def rank_tables(g: Graph, ctx) -> list[dict]:
    """Rank every table except mirror, copy-schema and family-copy ones. Strong signals: hub score (incoming relations),
    query-log hits, code refs, context mentions, area centrality. Weak generic signals: row count (log-scaled), relation
    degree, and a penalty for tables with zero or fewer than 10 rows (known only from the adapter's estimate or profiles).
    Returns [{"table", "score", "parts"}] best first; ties broken by table id. Same inputs give the same list."""
    skip = skipped_tables(g) | set(copy_of(families(g, ctx)))
    ids = sorted(t for t in g.nodes if t not in skip)
    incoming: dict[str, int] = {}
    for e in _real_edges(g):
        incoming[e["to"]] = incoming.get(e["to"], 0) + 1
    qhits, code, mentions = _evidence(ctx)
    area_of = {t: a for a in g.data.get("areas", []) for t in a["tables"]}
    inside: dict[str, int] = {}
    for e in _real_edges(g):
        if e.get("hub") or e.get("role"):
            continue
        if area_of.get(e["from"]) and area_of.get(e["from"]) == area_of.get(e["to"]):
            for t in (e["from"], e["to"]):
                inside[t] = inside.get(t, 0) + 1
    nrows = _rows(g, ctx)
    logrows = {t: math.log1p(n) for t, n in nrows.items()}
    presence = {t: PRESENCE["empty"] if nrows.get(t) == 0 else PRESENCE["tiny"] if t in nrows and nrows[t] < TINY_ROWS else 0.0
                for t in ids}
    comps = {"hub": _norm(incoming, ids), "queries": _norm(qhits, ids), "code": _norm(code, ids),
             "context": _norm(mentions, ids), "area": _norm(inside, ids), "rows": _norm(logrows, ids),
             "degree": _norm(_degree(g), ids), "presence": presence}
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
