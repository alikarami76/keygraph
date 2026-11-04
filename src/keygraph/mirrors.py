"""Mirror schemas: a schema whose tables duplicate another schema's tables (a BI or reporting copy, a replica
loaded under another name). Their tables would double every area, hub count and join path, so keygraph marks
them and keeps them out of areas, hubs and path search unless asked for."""

from __future__ import annotations

from keygraph.graph import Graph

MIRROR_SHARE = 0.8


def _copies(a: frozenset, b: frozenset | None) -> bool:
    """Two tables of the same name are copies when their column-name sets match or one contains the other
    (a reporting copy often adds a load timestamp or drops a wide column)."""
    return b is not None and (a == b or a <= b or b <= a)


def _structure(graph: Graph, schema: str, tables: set[str]) -> int:
    """How much a schema's copy of `tables` looks like a source database: secondary indexes, plus relations among
    those tables (declared count three times; role columns such as created_by_id do not count). A reporting copy
    usually keeps the columns but drops the keys."""
    from keygraph.hubs import is_role_column
    ids = {f"{schema}.{t}" for t in tables}
    ix = sum(1 for t in ids for i in graph.nodes[t]["indexes"] if not i["primary"])
    rel = sum(3 if e["kind"] == "declared" else 1 for e in graph.edges
              if e["from"] in ids and e["to"] in ids and e["from"] != e["to"] and not any(is_role_column(c) for c in e["from_columns"]))
    return ix + rel


def _original(graph: Graph, by: dict, a: str, b: str, common: set[str]) -> tuple[str, str, bool]:
    """Which of two schemas sharing `common` tables is the source: (original, reason, close call?).
    (a) the one with clearly more keys and relations on the shared tables, (b) then the one with fewer tables
    the other lacks (a copy tends to add load or log tables), (c) then the larger, (d) then the name."""
    sa, sb = _structure(graph, a, common), _structure(graph, b, common)
    if max(sa, sb) >= 2 and max(sa, sb) >= 1.25 * min(sa, sb) + 1:
        return (a if sa > sb else b), f"keys and relations ({max(sa, sb)} vs {min(sa, sb)})", False
    ea, eb = len(by[a]) - len(common), len(by[b]) - len(common)
    if ea != eb:
        return (a if ea < eb else b), f"fewer extra tables ({min(ea, eb)} vs {max(ea, eb)}); keys and relations look alike", True
    if len(by[a]) != len(by[b]):
        return (a if len(by[a]) > len(by[b]) else b), "size; keys, relations and extra tables look alike", True
    return min(a, b), "name only; the two schemas look alike", True


def detect_mirrors(graph: Graph, share: float = MIRROR_SHARE, min_tables: int = 2,
                   force: dict[str, str] | None = None, never: set[str] | frozenset = frozenset(),
                   warnings: list[str] | None = None) -> dict[str, str]:
    """{mirror schema: original schema}. Two schemas are a mirror pair when at least `share` of one schema's tables
    are copies (same name, see _copies) of the other's. Which side is the source is guessed by _original; a schema
    is marked a mirror only when it is the copy and at least `share` of its own tables are copies. A close guess, a
    `force` ({mirror: original}, from a context file) that disagrees with the guess, or a forced pair naming an
    unknown schema adds a line to `warnings`. `never` lists schemas that are never mirrors."""
    warn = warnings.append if warnings is not None else (lambda _m: None)
    by: dict[str, dict[str, frozenset]] = {}
    for n in graph.nodes.values():
        by.setdefault(n["schema"], {})[n["table"]] = frozenset(c["name"] for c in n["columns"])

    def common(x: str, y: str) -> set[str]:
        return {t for t, cols in by[x].items() if _copies(cols, by[y].get(t))}

    guess: dict[str, str] = {}
    why: dict[str, str] = {}
    seen: set[frozenset] = set()
    for s in sorted(by, key=lambda x: (-len(by[x]), x)):
        if len(by[s]) < min_tables:
            continue
        cands = [(len(common(s, o)), o) for o in by if o != s]
        hits, o = max(cands, default=(0, None))
        if o is None or hits / len(by[s]) < share or frozenset((s, o)) in seen:
            continue
        seen.add(frozenset((s, o)))
        shared = common(s, o)
        orig, reason, close = _original(graph, by, s, o, shared)
        copy = o if orig == s else s
        if len(common(copy, orig)) / len(by[copy]) < share:
            warn(f"mirror guess: {orig} and {copy} share {len(shared)} tables and {orig} looks like the source "
                 f"({reason}), but {copy} is not mostly copies, so neither is marked a mirror; confirm with mirrors.yaml")
            continue
        guess[copy], why[copy] = orig, reason
        if close:
            warn(f"mirror guess: {copy} copies {orig} (decided by {reason}) — confirm with mirrors.yaml")
    out = {m: o for m, o in guess.items() if m not in never}
    for m, o in (force or {}).items():
        if m not in by or o not in by or m == o:
            warn(f"mirrors.yaml: '{m}: {o}' ignored: unknown schema" if (m not in by or o not in by) else f"mirrors.yaml: '{m}: {o}' ignored")
            continue
        if guess.get(o) == m or (m in guess and guess[m] != o):
            g = f"{o} copies {m}" if guess.get(o) == m else f"{m} copies {guess[m]}"
            warn(f"mirror guess: mirrors.yaml says {m} copies {o}, which disagrees with the guess ({g}, by {why.get(o) or why.get(m)}); using mirrors.yaml")
        out.pop(o, None) if out.get(o) == m else None
        out[m] = o
    for m in list(out):  # an original is never itself a mirror: follow chains to the root
        root, hops = out[m], 0
        while root in out and hops < len(out):
            root, hops = out[root], hops + 1
        out[m] = root
    return dict(sorted((m, o) for m, o in out.items() if m != o))


def mark_mirrors(graph: Graph, mirrors: dict[str, str]) -> None:
    for n in graph.nodes.values():
        n.pop("mirror_of", None)
        if n["schema"] in mirrors:
            n["mirror_of"] = mirrors[n["schema"]]
    graph.data["mirrors"] = dict(sorted(mirrors.items()))


def mirror_tables(graph: Graph) -> set[str]:
    return {t for t, n in graph.nodes.items() if n.get("mirror_of")}
