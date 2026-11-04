"""Markdown + Mermaid pages per area and per table, for agents and docs."""

from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path

from keygraph.areas import area_of
from keygraph.graph import Graph
from keygraph.query import find_paths
from keygraph.summary import relation_summary

MERMAID_LIMIT = 25  # an area's Mermaid diagram draws its first (most connected) 25 tables


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def table_file(t: str) -> str:
    return re.sub(r"[^\w.\-]", "_", t) + ".md"


def _ident(text: str, fallback: str = "t") -> str:
    """A Mermaid-safe identifier: ASCII letters, digits and underscores, never starting with a digit."""
    n = re.sub(r"[^A-Za-z0-9_]", "_", str(text or ""))
    if not re.search(r"[A-Za-z0-9]", n):
        n = fallback
    return "t_" + n if n[0].isdigit() else n


def _names(graph: Graph, tables: list[str]) -> dict[str, str]:
    """Unique Mermaid entity names: bare table name, or schema_table when names collide."""
    bare = {t: _ident(graph.nodes[t]["table"]) for t in tables}
    count: dict[str, int] = {}
    for n in bare.values():
        count[n] = count.get(n, 0) + 1
    names: dict[str, str] = {}
    used: set[str] = set()
    for t in tables:
        n = bare[t]
        if count[n] > 1:
            n = _ident(graph.nodes[t]["schema"] + "_" + n)
        base, i = n, 2
        while n in used:
            n, i = f"{base}_{i}", i + 1
        used.add(n)
        names[t] = n
    return names


def _edge_index(graph: Graph) -> dict[str, list[dict]]:
    """table id -> the edges that start at it (built once per docs run)."""
    idx: dict[str, list[dict]] = {}
    for e in graph.edges:
        idx.setdefault(e["from"], []).append(e)
    return idx


def mermaid_area(graph: Graph, area: dict, limit: int = MERMAID_LIMIT, out_edges: dict[str, list[dict]] | None = None) -> str:
    tables = area["tables"][:limit]
    inside = set(tables)
    ent = _names(graph, tables)
    out_edges = out_edges if out_edges is not None else _edge_index(graph)
    lines = ["erDiagram"]
    for t in tables:
        n = graph.nodes[t]
        mine = out_edges.get(t, [])
        rel = [e for e in mine if not (e.get("hub") or e.get("role"))]
        keys = set(n.get("primary_key", [])) | {c for e in rel for c in e["from_columns"]}
        if ent[t] != n["table"]:
            lines.append(f"    %% {ent[t]} is {t}")
        lines.append(f"    {ent[t]} {{")
        for c in n.get("columns", []):
            if c["name"] in keys:
                typ = _ident((c["type"] or "col").split("(")[0], "col")
                tag = " PK" if c["name"] in n.get("primary_key", []) else " FK"
                lines.append(f"        {typ} {_ident(c['name'], 'col')}{tag}")
        lines.append("    }")
        for e in mine:
            if e.get("hub") or e.get("role"):
                kind = "hub" if e.get("hub") else "role"
                lines.append(f"    %% {ent[t]} {kind} ref to {e['to']} via {', '.join(e['from_columns'])}")
    for t in tables:
        for e in out_edges.get(t, []):
            if e.get("hub") or e.get("role") or e["to"] not in inside or e["from"] == e["to"]:
                continue
            left = "||" if e.get("cardinality") == "one-to-one" else "}o"
            col = ", ".join(e["from_columns"]).replace('"', "'")
            lines.append(f'    {ent[e["from"]]} {left}--|| {ent[e["to"]]} : "{col}"')
    return "\n".join(lines)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _table_md(graph: Graph, t: str, areas: list[dict], name_of: dict, ctx_notes: dict,
              touching: dict[str, list[dict]] | None = None, of: dict[str, str] | None = None) -> str:
    n, s = graph.nodes[t], relation_summary(graph, t, areas, edges=None if touching is None else touching.get(t, []), of=of)
    note = ctx_notes.get(t, {})
    out = [f"# {t}", "", f"Area: {name_of.get(s['area'], '-')}", ""]
    if note.get("grain"):
        out += [f"One row: {note['grain']}", ""]
    out += ["## Keys and indexes", "", f"- primary key: {', '.join(n['primary_key']) or '(none)'}"]
    out += [f"- {ix['name']}{' (unique)' if ix.get('unique') else ''}: {', '.join(ix['columns'])}" for ix in n["indexes"]]
    out += ["", "## Columns", "", "| column | type | nullable |", "|---|---|---|"]
    out += [f"| {c['name']} | {c['type']} | {'yes' if c.get('nullable', True) else 'no'} |" for c in n["columns"]]
    for title, groups in (("Points to", s["out"]), ("Pointed to by", s["in"])):
        out += ["", f"## {title}", ""]
        for g in groups:
            more = f" (+{g['count'] - 8} more)" if g["count"] > 8 else ""
            out.append(f"- via `{g['column']}`: {', '.join(g['tables'][:8])}{more}")
        if not groups:
            out.append("- (none)")
    if s["hub_labels"]:
        out += ["", "Hub references: " + ", ".join(f"{h['to']} via {', '.join(h['columns'])}" for h in s["hub_labels"])]
    return "\n".join(out) + "\n"


def _degrees(graph: Graph) -> dict[str, int]:
    d: dict[str, int] = {}
    for e in graph.edges:
        if e.get("hub") or e.get("role") or e["confidence"] < 0.6 or e["from"] == e["to"]:
            continue
        d[e["from"]] = d.get(e["from"], 0) + 1
        d[e["to"]] = d.get(e["to"], 0) + 1
    return d


def _join_paths(graph: Graph, area: dict, deg: dict[str, int]) -> list[str]:
    lines = []
    top = sorted(area["tables"], key=lambda t: (-deg.get(t, 0), t))[:5]
    for a, b in combinations(top, 2):
        paths = find_paths(graph, a, b, 3, 0.6, 1, max_pops=20000)
        if paths:
            lines.append(f"- {' -> '.join(paths[0]['tables'])}")
    return lines


def _neighbours(graph: Graph, areas: list[dict]) -> dict[str, dict[str, int]]:
    """Per area id: other area id -> count of non-hub, non-role edges (confidence >= 0.6) crossing in either direction."""
    of = {t: a["id"] for a in areas for t in a["tables"]}
    res: dict[str, dict[str, int]] = {}
    for e in graph.edges:
        if e.get("hub") or e.get("role") or e["confidence"] < 0.6:
            continue
        a, b = of.get(e["from"]), of.get(e["to"])
        if a and b and a != b:
            res.setdefault(a, {})[b] = res.setdefault(a, {}).get(b, 0) + 1
            res.setdefault(b, {})[a] = res.setdefault(b, {}).get(a, 0) + 1
    return res


def write_docs(out: Path, graph: Graph, areas: list[dict], ctx_dump: dict | None) -> dict:
    out = Path(out)
    (out / "areas").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    name_of = {a["id"]: a["name"] for a in areas}
    ctx_notes = {x["table"]: x for x in (ctx_dump or {}).get("tables", [])}
    written: list[str] = ["areas/index.md"]
    idx = ["# Areas", "", "| area | tables | top tables |", "|---|---|---|"]
    used: set[str] = set()
    fname: dict[str, str] = {}
    for a in areas:
        fn = slug(a["name"]) or "area"
        if fn in used or fn == "index":
            fn = f"{fn}-{a['id']}"
        used.add(fn)
        fname[a["id"]] = fn
    nb, deg = _neighbours(graph, areas), _degrees(graph)
    out_edges, of = _edge_index(graph), area_of(areas)
    touching: dict[str, list[dict]] = {}
    for e in graph.edges:
        touching.setdefault(e["from"], []).append(e)
        if e["to"] != e["from"]:
            touching.setdefault(e["to"], []).append(e)
    for a in areas:
        fn = fname[a["id"]]
        idx.append(f"| [{a['name']}]({fn}.md) | {a['size']} | {', '.join(a['top'])} |")
        shown = f" The diagram shows the {MERMAID_LIMIT} most connected." if a["size"] > MERMAID_LIMIT else ""
        body = [f"# {a['name']}", "", f"{_plural(a['size'], 'table')}{' (curated)' if not a.get('auto', True) else ''}.{shown}", "",
                "```mermaid", mermaid_area(graph, a, out_edges=out_edges), "```", ""]
        paths = _join_paths(graph, a, deg)
        if paths:
            body += ["## Main join paths", ""] + paths + [""]
        links = sorted(nb.get(a["id"], {}).items(), key=lambda kv: (-kv[1], name_of[kv[0]]))
        body += ["## Neighbouring areas", ""]
        body += [f"- [{name_of[o]}]({fname[o]}.md): {_plural(n, 'relation')}" for o, n in links] or ["- (none)"]
        body += ["", "## Tables", ""]
        body += [f"- {t}: {ctx_notes.get(t, {}).get('grain', '')}".rstrip(": ") for t in a["tables"]]
        (out / "areas" / f"{fn}.md").write_text("\n".join(body) + "\n", encoding="utf-8")
        written.append(f"areas/{fn}.md")
    (out / "areas" / "index.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    for t in graph.nodes:
        (out / "tables" / table_file(t)).write_text(_table_md(graph, t, areas, name_of, ctx_notes, touching, of), encoding="utf-8")
        written.append(f"tables/{table_file(t)}")
    return {"areas": len(areas), "tables": len(graph.nodes), "files": written}
