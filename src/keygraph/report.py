"""RELATIONS_REPORT.md: plain-language review of the graph."""

from __future__ import annotations

from .graph import Graph


def _leading(node: dict, cols: list[str]) -> bool:
    n = len(cols)
    return any(len(i["columns"]) >= n and set(i["columns"][:n]) == set(cols) for i in node["indexes"])


def write_report(graph: Graph, ctx=None, cap: int = 40) -> str:
    st = graph.data["stats"]
    nodes = graph.nodes
    deg: dict[str, int] = {n: 0 for n in nodes}
    for e in graph.edges:
        deg[e["from"]] += 1
        if e["to"] != e["from"]:
            deg[e["to"]] += 1
    out = ["# Relations report", ""]
    out.append(f"{st['tables']} tables in {st['schemas']} schema(s), {st['columns']} columns, {st['indexes']} indexes and "
               f"{st['edges']} relations. Declared foreign keys: {st['edges_by_kind'].get('declared', 0)}; inferred: "
               f"{st['edges_by_kind'].get('inferred', 0)}. Inferred relations are guesses from names and key shapes: "
               "treat them by their confidence.")
    out += ["", "Confidence bands: " + ", ".join(f"{k}: {v}" for k, v in st["edges_by_confidence"].items()), ""]
    areas = graph.data.get("areas") or []
    if areas:
        big = max(areas, key=lambda a: a["size"])
        out += [f"Areas: {len(areas)}; largest is {big['name']} with {big['size']} tables. Hub tables: {len(graph.data.get('hubs', []))}.", ""]

    def section(title, rows, note=""):
        out.append(f"## {title} ({len(rows)})")
        if note:
            out.append(note)
        out.append("")
        if not rows:
            out.append("None.")
        for r in rows[:cap]:
            out.append("- " + r)
        if len(rows) > cap:
            out.append(f"- ... and {len(rows) - cap} more (see graph.json)")
        out.append("")

    hubs = sorted(nodes, key=lambda n: (-deg[n], n))[:10]
    section("Hub tables", [f"{n}: {deg[n]} relations" for n in hubs if deg[n]],
            "Tables with the most relations; joins usually route through them.")
    section("Tables with no relations", [n for n in sorted(nodes) if deg[n] == 0],
            "No declared or inferred relation found. They may be logs, config tables, or use keys with unusual names.")
    weak = [e for e in graph.edges if e["kind"] == "inferred" and (e["confidence"] < 0.6 or e["ambiguous"])]
    weak.sort(key=lambda e: (e["confidence"], e["id"]))
    section("Ambiguous or low-confidence relations to review", [
        f"{e['id']} confidence {e['confidence']:.2f}{' ambiguous' if e['ambiguous'] else ''}: {'; '.join(e['evidence'][:2])}" for e in weak],
        "Confirm or reject these in an overrides file.")
    poly = [f"{n['id']}.{p['id_column']} (type in {p['type_column']})" for n in nodes.values() for p in n["polymorphic"]]
    section("Polymorphic columns", poly, "The target table depends on a type column; there is no single join target.")
    section("Tables without a primary key", [n for n in sorted(nodes) if not nodes[n]["primary_key"]],
            "Row identity is unclear; joins into these tables may duplicate rows.")
    red = [f"{n['id']}: {r['index']}({', '.join(r['columns'])}) is covered by {r['covered_by']} ({r['reason']})"
           for n in nodes.values() for r in n["redundant_indexes"]]
    section("Redundant indexes", red)
    unidx = sorted({f"{e['from']}({', '.join(e['from_columns'])}) -> {e['to']}" for e in graph.edges
                    if e["confidence"] >= 0.5 and not _leading(nodes[e["from"]], e["from_columns"])
                    and not any(set(nodes[e['from']]['primary_key']) >= set(e['from_columns']) for _ in [0])})
    section("Foreign-key-like columns without an index", unidx,
            "Joining or filtering on these columns scans the whole table.")
    unres = [f"{n['id']}: {', '.join(n['unresolved_id_columns'])}" for n in nodes.values() if n["unresolved_id_columns"]]
    section("Id-like columns with no matching table", unres)
    if graph.data["warnings"]:
        section("Warnings", graph.data["warnings"])
    if ctx is not None:
        notes = {r["table_id"]: r for r in ctx.rows("table_notes")}
        sem = {r["table_id"] for r in ctx.rows("column_semantics")}
        linked = {r["table_id"] for r in ctx.rows("concept_links")}
        covered = set(notes) | sem | linked
        c = ctx.counts()
        out.append("## Context coverage")
        out.append("")
        out.append(f"{len(covered & set(nodes))} of {len(nodes)} tables have any business context. Entries: " +
                   ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in c.items()) + ".")
        out.append("")
        nohub = [f"{n}: {deg[n]} relations" for n in sorted(nodes, key=lambda n: (-deg[n], n))
                 if deg[n] >= 3 and not (notes.get(n) or {}).get("grain")][:cap]
        section("Hub tables with no grain note", nohub)
        unv = [f"table note {k}" for k, v in notes.items() if not v["verified"]]
        unv += [f"column {r['table_id']}.{r['col']}" for r in ctx.rows("column_semantics") if not r["verified"]]
        unv += [f"concept {r['name']}" for r in ctx.rows("concepts") if not r["verified"]]
        section("Unverified context entries", sorted(unv))
        section("Tables with no context (first by relations)",
                [n for n in sorted(nodes, key=lambda n: (-deg[n], n)) if n not in covered][:cap],
                f"{len([n for n in nodes if n not in covered])} tables in total.")
    return "\n".join(out).rstrip() + "\n"
