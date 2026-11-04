"""Business areas: deterministic clusters of tables, ignoring hub and role edges."""

from __future__ import annotations

import hashlib
import random
from collections import Counter

from keygraph.graph import Graph


def _weights(graph: Graph, skip: set[str] = frozenset()) -> dict[str, dict[str, float]]:
    w: dict[str, dict[str, float]] = {t: {} for t in graph.nodes if t not in skip}
    for e in graph.edges:
        if e.get("hub") or e.get("role") or e["from"] == e["to"] or e["from"] in skip or e["to"] in skip:
            continue
        a, b, c = e["from"], e["to"], float(e["confidence"])
        w[a][b] = w[a].get(b, 0) + c
        w[b][a] = w[b].get(a, 0) + c
    return w


def _propagate(w, seed: int, rounds: int) -> dict[str, str]:
    rng = random.Random(seed)
    label = {n: n for n in w}
    order = sorted(w)
    for _ in range(rounds):
        rng.shuffle(order)
        changed = 0
        for n in order:
            score: dict[str, float] = {}
            for m, x in w[n].items():
                score[label[m]] = score.get(label[m], 0) + x
            if not score:
                continue
            best = max(sorted(score), key=lambda l: score[l])
            if best != label[n]:
                label[n], changed = best, changed + 1
        if not changed:
            break
    return label


def _stem(word: str) -> str:
    """Drop a plural 's', but keep words such as status, address and analysis whole."""
    return word if word.endswith(("ss", "us", "is")) else word.removesuffix("s")


PREFIX_SHARE = 0.6


def _name(tables: list[str], graph: Graph) -> str:
    """The shared table-name prefix when it covers at least PREFIX_SHARE of the area ("Order" for orders,
    order_items, order_notes), else the most connected table that is not a hub (tables arrive ordered by
    connections). A minority prefix or a hub's name would mislead."""
    names = [graph.nodes[t]["table"] for t in tables]
    heads = Counter(_stem(n.split("_")[0]) for n in names)
    head, n = heads.most_common(1)[0]
    if n >= 2 and n / len(names) >= PREFIX_SHARE:
        return head.capitalize()
    hubs = set(graph.data.get("hubs") or [])
    return next((graph.nodes[t]["table"] for t in tables if t not in hubs), names[0])


def _id(top: list[str]) -> str:
    return "a_" + hashlib.sha1("|".join(sorted(top)).encode()).hexdigest()[:8]


def cluster_areas(graph: Graph, min_size: int = 3, seed: int = 7, rounds: int = 30) -> list[dict]:
    """Areas over the non-mirror tables, plus one area per mirror schema (flagged `mirror`), and one
    "Unconnected (<schema>)" area per schema for tables with no remaining edges (flagged `unconnected`)."""
    mirrors = {t for t, n in graph.nodes.items() if n.get("mirror_of")}
    w = _weights(graph, mirrors)
    degree = {t: len(v) for t, v in w.items()}
    label = _propagate(w, seed, rounds)
    groups: dict[str, set[str]] = {}
    for t, l in label.items():
        groups.setdefault(l, set()).add(t)
    merged = True
    while merged:  # repeat passes: a target that grew may now be small enough to merge again
        merged = False
        for l in sorted(groups, key=lambda l: (len(groups[l]), l)):  # merge small groups into their heaviest neighbour
            g = groups.get(l)
            if g is None or len(g) >= min_size:
                continue
            pull: dict[str, float] = {}
            for t in sorted(g):
                for m, x in sorted(w[t].items()):
                    if label[m] != l:
                        pull[label[m]] = pull.get(label[m], 0) + x
            if pull:
                target = max(sorted(pull), key=lambda k: pull[k])
                groups[target] |= g
                for t in sorted(g):
                    label[t] = target
                del groups[l]
                merged = True
    areas, loose = [], {}
    for g in groups.values():
        if len(g) < min_size and all(not w[t] for t in g):
            for t in g:
                loose.setdefault(graph.nodes[t]["schema"], []).append(t)
            continue
        tables = sorted(g, key=lambda t: (-degree[t], t))
        areas.append({"id": _id(tables[:3]), "name": _name(tables, graph), "tables": tables, "top": tables[:3],
                      "size": len(tables), "auto": True})
    for schema, ts in sorted(loose.items()):
        ts = sorted(ts)
        areas.append({"id": _id([f"unconnected:{schema}"]), "name": f"Unconnected ({schema})", "tables": ts,
                      "top": ts[:3], "size": len(ts), "auto": True, "unconnected": True})
    seen: Counter = Counter(a["name"] for a in areas)
    for a in areas:
        if seen[a["name"]] > 1 and not a.get("unconnected"):
            top = graph.nodes[a["top"][0]]
            a["name"] = (f"{a['name']} ({top['schema']})" if a["name"] == top["table"]
                         else f"{a['name']} ({top['schema']}: {top['table']})")
    by_schema: dict[str, list[str]] = {}
    for t in mirrors:
        by_schema.setdefault(graph.nodes[t]["schema"], []).append(t)
    deg: Counter = Counter()
    for e in graph.edges:  # one pass, not one per table
        deg[e["from"]] += 1
        if e["to"] != e["from"]:
            deg[e["to"]] += 1
    for schema, ts in sorted(by_schema.items()):
        ts = sorted(ts, key=lambda t: (-deg[t], t))
        of = graph.nodes[ts[0]]["mirror_of"]
        areas.append({"id": _id([f"mirror:{schema}"]), "name": f"Mirror of {of} ({schema})", "tables": ts, "top": ts[:3],
                      "size": len(ts), "auto": True, "mirror": schema, "mirror_of": of})
    return sorted(areas, key=lambda a: (-a["size"], a["name"]))


def area_of(areas: list[dict]) -> dict[str, str]:
    return {t: a["id"] for a in areas for t in a["tables"]}


def area_links(graph: Graph, areas: list[dict]) -> list[dict]:
    of = area_of(areas)
    c: Counter = Counter()
    mirrors = {t for t, n in graph.nodes.items() if n.get("mirror_of")}
    for e in graph.edges:
        if e.get("hub") or e.get("role") or e["from"] in mirrors or e["to"] in mirrors:
            continue
        a, b = of.get(e["from"]), of.get(e["to"])
        if a and b and a != b:
            c[tuple(sorted((a, b)))] += 1
    return [{"a": a, "b": b, "edges": n} for (a, b), n in c.most_common()]


def apply_curation(graph: Graph, areas: list[dict], rules: list[dict]) -> tuple[list[dict], list[str]]:
    areas = [dict(a, tables=list(a["tables"])) for a in areas]
    warns: list[str] = []

    def find(key: str):
        key = str(key)
        return next((a for a in areas if a["id"] == key or a["name"].lower() == key.lower()
                     or key in a["tables"]), None)

    def move(t: str, dest: dict):
        for a in areas:
            if t in a["tables"]:
                a["tables"].remove(t)
        dest["tables"].append(t)

    for r in rules:
        if not isinstance(r, dict) or not (r.get("name") or r.get("match") or r.get("merge")):
            warns.append(f"area rule {r!r} ignored: it needs a name, a match or a merge")
            continue
        if r.get("merge"):
            hits = [find(k) for k in r["merge"]]
            if any(h is None for h in hits):
                warns.append(f"merge {r['merge']}: an area was not found")
                continue
            dest = hits[0]
            for h in hits[1:]:
                if h is not dest:
                    dest["tables"] += h["tables"]
                    h["tables"] = []
            dest["auto"] = False
            continue
        dest = next((find(k) for k in r.get("match", []) if find(k)), None)
        if dest is None:
            if r.get("match"):
                warns.append(f"area rule '{r.get('name')}': no area matches {r['match']}")
                continue
            if not r.get("name"):
                warns.append(f"area rule {r!r} ignored: it needs a name, a match or a merge")
                continue
            dest = {"id": _id([f"curated:{r['name']}"]), "name": r["name"], "tables": [], "auto": False}
            areas.append(dest)
        dest["name"], dest["auto"] = r.get("name", dest["name"]), False
        for t in r.get("pin", []):
            try:
                move(graph.resolve(t), dest)
            except ValueError:  # UnknownTable and AmbiguousTable both subclass ValueError
                warns.append(f"area '{dest['name']}': unknown table {t}")
        for t in r.get("exclude", []):
            try:
                tid = graph.resolve(t)
            except ValueError:
                warns.append(f"area '{dest['name']}': unknown table {t}")
                continue
            if tid in dest["tables"]:
                dest["tables"].remove(tid)
                schema = graph.nodes[tid]["schema"]
                loose = find(f"Unconnected ({schema})") or {"id": _id([f"unconnected:{schema}"]),
                                                            "name": f"Unconnected ({schema})", "tables": [], "auto": True,
                                                            "unconnected": True}
                if loose not in areas:
                    areas.append(loose)
                loose["tables"].append(tid)
    deg: Counter = Counter()
    for e in graph.edges:  # one pass over the edges
        if not e.get("hub"):
            deg[e["from"]] += 1
            if e["to"] != e["from"]:
                deg[e["to"]] += 1
    out = []
    for a in areas:
        if not a["tables"]:
            continue
        a["tables"] = sorted(set(a["tables"]), key=lambda t: (-deg[t], t))
        a["top"], a["size"] = a["tables"][:3], len(a["tables"])
        out.append(a)
    return sorted(out, key=lambda a: (-a["size"], a["name"])), warns
