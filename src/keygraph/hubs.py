"""Hub tables: tables so many others point to that drawing their edges would swamp every view."""

from __future__ import annotations

import re

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
    if counts:
        cut = counts[min(len(counts) - 1, int(len(counts) * percentile / 100))]
        hubs |= {t for t, n in incoming.items() if n >= max(cut, 5)}
        hubs |= {t for t, n in incoming.items() if n >= 5 and role_in.get(t, 0) / n >= role_share}
    hubs |= {graph.resolve(t) for t in include}
    hubs -= {graph.resolve(t) for t in exclude}
    return hubs


def mark_hubs(graph: Graph, hubs: set[str], patterns=ROLE_PATTERNS) -> None:
    for e in graph.edges:
        e["role"] = _edge_is_role(e, patterns)
        e["hub"] = e["to"] in hubs and e["from"] != e["to"]
    graph.data["hubs"] = sorted(hubs)
