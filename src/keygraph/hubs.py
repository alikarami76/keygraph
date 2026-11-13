"""Hub tables: tables so many others point to that drawing their edges would swamp every view."""

from __future__ import annotations

import re

from keygraph import devlog
from keygraph.graph import Graph

ROLE_PATTERNS = [r"(^|_)(created|updated|deleted|modified|approved|rejected|changed|edited)_by(_id)?$",
                 r"^(owner|author|creator|editor|operator|admin)_id$", r"_by_id$", r"_by$"]


def is_role_column(name: str, patterns=ROLE_PATTERNS) -> bool:
    low = name.lower()
    return any(re.search(p, low) for p in patterns)


def _edge_is_role(e: dict, patterns) -> bool:
    return any(is_role_column(c, patterns) for c in e["from_columns"])


def detect_hubs(graph: Graph, percentile: float = 99, role_share: float = 0.6, include=(), exclude=(),
                patterns=ROLE_PATTERNS) -> set[str]:
    incoming: dict[str, int] = {}
    role_in: dict[str, int] = {}
    mirrors = {t for t, n in graph.nodes.items() if n.get("mirror_of")}  # a copy's edges would double the counts
    for e in graph.edges:
        if e["from"] == e["to"] or e["from"] in mirrors or e["to"] in mirrors:
            continue
        incoming[e["to"]] = incoming.get(e["to"], 0) + 1
        if _edge_is_role(e, patterns):
            role_in[e["to"]] = role_in.get(e["to"], 0) + 1
    counts = sorted(incoming.values())
    hubs: set[str] = set()
    cut = None
    why: dict[str, str] = {}
    if counts:
        cut = counts[min(len(counts) - 1, int(len(counts) * percentile / 100))]
        by_degree = {t for t, n in incoming.items() if n >= max(cut, 5)}
        by_role = {t for t, n in incoming.items() if n >= 5 and role_in.get(t, 0) / n >= role_share} - by_degree
        hubs |= by_degree | by_role
        if devlog.active():
            why = {**{t: f"incoming relations {incoming[t]} >= threshold {max(cut, 5)}" for t in by_degree},
                   **{t: f"{role_in.get(t, 0)} of {incoming[t]} incoming relations are role columns (>= {role_share:.0%})" for t in by_role}}
    inc = {graph.resolve(t) for t in include}
    exc = {graph.resolve(t) for t in exclude}
    hubs |= inc
    hubs -= exc
    if devlog.active():
        devlog.trace("hub.threshold", percentile=percentile, cut=cut, threshold=max(cut, 5) if cut is not None else None,
                     tables_with_incoming=len(incoming), picked=len(hubs))
        for t in sorted(set(incoming) | inc | exc):
            if incoming.get(t, 0) < 5 and t not in inc and t not in exc:
                continue
            devlog.trace("hub.decision", table=t, incoming=incoming.get(t, 0), role_incoming=role_in.get(t, 0),
                         verdict="excluded" if t in exc else "picked" if t in hubs else "not a hub",
                         reason="excluded by hubs.exclude" if t in exc else "included by hubs.include" if t in inc and t not in why
                         else why.get(t, f"incoming relations {incoming.get(t, 0)} below threshold"))
    return hubs


def mark_hubs(graph: Graph, hubs: set[str], patterns=ROLE_PATTERNS) -> None:
    for e in graph.edges:
        e["role"] = _edge_is_role(e, patterns)
        e["hub"] = e["to"] in hubs and e["from"] != e["to"]
    graph.data["hubs"] = sorted(hubs)
