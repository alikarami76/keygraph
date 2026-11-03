"""keygraph command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

from .context import ContextDB
from .explorer import build_html
from .graph import AmbiguousTable, Graph, UnknownTable, build_graph
from .infer import Options
from .model import Schema
from .overrides import Overrides
from .report import write_report
from . import query as Q
from . import search as S

DEFAULT_DIR = os.environ.get("KEYGRAPH_DIR", "keygraph-out")


def _dir_args(p):
    p.add_argument("--dir", default=DEFAULT_DIR, help="output directory holding graph.json and keygraph.db (default keygraph-out)")


def _json_arg(v: str | None) -> dict:
    if not v:
        return {}
    if v.startswith("{"):
        return json.loads(v)
    return json.loads(Path(v).read_text(encoding="utf-8"))


def _emit(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1) if getattr(args, "json", False) else text)


def _load(args) -> Graph:
    path = Path(args.dir) / "graph.json"
    if not path.exists():
        raise SystemExit(f"no graph at {path}; run `keygraph scan` or `keygraph load` first")
    return Graph.load(path)


def _ctx(args, g: Graph) -> ContextDB:
    return ContextDB(Path(args.dir) / "keygraph.db", g)


def render(out: Path, g: Graph, ctx: ContextDB | None) -> dict:
    """Write graph.json, RELATIONS_REPORT.md and keygraph.html."""
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "graph.json")
    (out / "RELATIONS_REPORT.md").write_text(write_report(g, ctx), encoding="utf-8")
    html = build_html(g, ctx.dump() if ctx else None)
    (out / "keygraph.html").write_text(html, encoding="utf-8")
    return {"graph.json": (out / "graph.json").stat().st_size, "RELATIONS_REPORT.md": (out / "RELATIONS_REPORT.md").stat().st_size,
            "keygraph.html": len(html.encode("utf-8"))}


def run_pipeline(schema: Schema, out: str | Path, overrides: str | None = None, context: list[str] | None = None,
                 notes: list[dict] | None = None, options: Options | None = None, verbose=True) -> dict:
    out = Path(out)
    t0 = time.time()
    ov = Overrides.load(overrides) if overrides else None
    g = build_graph(schema, ov, options)
    ctx = ContextDB(out / "keygraph.db", g)
    ctx.index_graph(g)
    for path in context or []:
        ctx.import_path(path)
    ctx.reindex()
    S.apply_context_to_graph(g, ctx)
    sizes = render(out, g, ctx)
    result = {"stats": g.data["stats"], "context": ctx.counts(), "files": sizes, "seconds": round(time.time() - t0, 1),
              "warnings": g.data["warnings"][:10] + ctx.warnings[:10]}
    ctx.close()
    if verbose:
        st = result["stats"]
        print(f"keygraph: {st['tables']} tables, {st['columns']} columns, {st['indexes']} indexes, {st['edges']} edges "
              f"{st['edges_by_kind']} -> {out}/ ({result['seconds']}s)")
    return result


def cmd_scan(a):
    from .adapters.live import scan_url
    run_pipeline(scan_url(a.url, a.schema, a.all_schemas), a.dir, a.overrides, a.context)


def cmd_load(a):
    from .adapters.dump import load_dump
    filt = None
    if a.where:
        pairs = [w.split("=", 1) for w in a.where]
        filt = lambda r: all(str(r.get(k)) == v for k, v in pairs)  # noqa: E731
    cm = _json_arg(a.columns_map)
    if a.flatten:
        cm["_flatten"] = a.flatten
    sch = load_dump(a.columns, a.statistics, a.key_columns, a.default_schema, cm, _json_arg(a.statistics_map),
                    _json_arg(a.kcu_map), filt)
    run_pipeline(sch, a.dir, a.overrides, a.context)


def cmd_render(a):
    g = _load(a)
    ctx = _ctx(a, g)
    ctx.index_graph(g)
    ctx.reindex()
    print(json.dumps(render(Path(a.dir), g, ctx)))
    ctx.close()


def cmd_probe(a):
    from .probe import SelectOnlyRunner, probe_graph
    g = _load(a)
    runner = SelectOnlyRunner.from_url(a.url)
    quote = "`" if a.url.startswith("mysql") else '"'
    res = probe_graph(g, runner, a.sample, quote, a.max_edges, a.min_confidence)
    ctx = _ctx(a, g)
    render(Path(a.dir), g, ctx)
    ctx.close()
    print(json.dumps(res, indent=1))


def cmd_path(a):
    g = _load(a)
    ps = Q.find_paths(g, a.a, a.b, a.max_hops, a.min_confidence, a.k)
    _emit(a, {"from": g.resolve(a.a), "to": g.resolve(a.b), "paths": ps}, Q.format_paths(a.a, a.b, ps))


def cmd_neighbors(a):
    g = _load(a)
    r = Q.neighbors(g, a.table, a.depth, a.min_confidence)
    _emit(a, r, Q.format_neighbors(r))


def cmd_explain(a):
    g = _load(a)
    r = Q.explain(g, a.ref)
    _emit(a, r, Q.format_explain(r))


def cmd_keys(a):
    g = _load(a)
    r = Q.keys(g, a.table)
    _emit(a, r, Q.format_keys(r))


def cmd_search(a):
    g = _load(a)
    ctx = _ctx(a, g)
    r = S.search(g, ctx, " ".join(a.query), a.limit)
    ctx.close()
    _emit(a, r, S.format_search(r))


def cmd_describe(a):
    g = _load(a)
    ctx = _ctx(a, g)
    r = S.describe(g, ctx, a.table)
    ctx.close()
    _emit(a, r, S.format_describe(r))


def _kv(pairs: list[str]) -> dict:
    d: dict = {}
    for p in pairs:
        k, _, v = p.partition("=")
        if k == "codes":
            d[k] = dict(x.split(":", 1) for x in v.split(",") if ":" in x)
        elif k == "link":
            d.setdefault("links", []).append(dict(zip(("table", "column", "role"), (v.split(":") + ["", ""])[:3])))
        else:
            d[k] = v
    return d


def cmd_context(a):
    g = _load(a)
    ctx = _ctx(a, g)
    if a.action == "import":
        counts = ctx.import_path(a.path, a.verified)
    elif a.action == "import-notes":
        from .notes import notes_to_context
        data = notes_to_context(a.path, a.schema_key, a.default_schema)
        if a.out:
            if a.out.endswith(".json"):
                Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            else:
                import yaml
                Path(a.out).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for kind, key in (("table", "tables"), ("column", "columns")):
            for item in data[key]:
                ctx.upsert(kind, item, item.get("source", ""), item.get("verified", False))
        ctx.reindex()
        counts = {"tables": len(data["tables"]), "columns": len(data["columns"])}
    elif a.action == "add":
        d = _kv(a.fields)
        ctx.upsert(a.kind, d, d.get("source") or "agent", d.get("verified") in ("true", "yes", "1"))
        ctx.reindex()
        counts = {a.kind: 1}
    elif a.action == "export":
        data = ctx.dump()
        text = json.dumps(data, ensure_ascii=False, indent=1)
        if a.out and not a.out.endswith(".json"):
            import yaml
            text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        (Path(a.out).write_text(text, encoding="utf-8") if a.out else print(text))
        ctx.close()
        return
    else:  # stats
        print(json.dumps(ctx.counts(), indent=1))
        ctx.close()
        return
    S.apply_context_to_graph(g, ctx)
    render(Path(a.dir), g, ctx)
    print(json.dumps({"imported": counts, "warnings": ctx.warnings}, ensure_ascii=False, indent=1))
    ctx.close()


def cmd_fetch(a):
    from .adapters.fetch import command_runner, fetch_metadata
    run = command_runner(a.cmd, a.timeout)
    stats, kcu = fetch_metadata(run, a.schema_expr, a.limit)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("statistics.jsonl", stats), ("key_columns.jsonl", kcu)):
        (out / name).write_text("\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + "\n", encoding="utf-8")
    print(f"fetched {len(stats)} index rows and {len(kcu)} foreign-key rows into {out}")


def cmd_ui(a):
    p = (Path(a.dir) / "keygraph.html").resolve()
    if not p.exists():
        raise SystemExit(f"{p} not found; run a build first")
    print(p)
    webbrowser.open(p.as_uri())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="keygraph", description="Map database keys and table relations; find join paths.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, *aliases):
        p = sub.add_parser(name, help=help_, aliases=list(aliases))
        p.set_defaults(fn=fn)
        _dir_args(p)
        return p

    def build_opts(p):
        p.add_argument("--overrides", help="YAML/JSON overrides file")
        p.add_argument("--context", action="append", help="context YAML/JSON file or directory to import (repeatable)")

    p = add("scan", cmd_scan, "scan a live database through SQLAlchemy (metadata only)")
    p.add_argument("--url", required=True)
    p.add_argument("--schema", action="append")
    p.add_argument("--all-schemas", action="store_true")
    build_opts(p)
    p = add("load", cmd_load, "build from information_schema dumps (JSON/JSONL/CSV)", "build")
    p.add_argument("--columns", required=True)
    p.add_argument("--statistics")
    p.add_argument("--key-columns")
    p.add_argument("--default-schema", default="main")
    p.add_argument("--columns-map", help="JSON (inline or file): canonical field -> your field, e.g. {\"TABLE_SCHEMA\":\"db\"}")
    p.add_argument("--statistics-map")
    p.add_argument("--kcu-map")
    p.add_argument("--flatten", action="append", help="flatten a nested dict field (e.g. raw) into the row")
    p.add_argument("--where", action="append", help="keep only COLUMNS rows with field=value (repeatable)")
    build_opts(p)
    add("render", cmd_render, "rewrite report and explorer from graph.json + keygraph.db")
    p = add("probe", cmd_probe, "sample value overlap for inferred edges (SELECT only)")
    p.add_argument("--url", required=True)
    p.add_argument("--sample", type=int, default=500)
    p.add_argument("--max-edges", type=int, default=200)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p = add("path", cmd_path, "best join paths between two tables")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--min-confidence", type=float, default=0.6)
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p = add("neighbors", cmd_neighbors, "tables related to a table")
    p.add_argument("table")
    p.add_argument("--depth", type=int, default=1)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--json", action="store_true")
    p = add("explain", cmd_explain, "what a column references / is referenced by, and why")
    p.add_argument("ref")
    p.add_argument("--json", action="store_true")
    p = add("keys", cmd_keys, "primary key, unique keys and indexes of a table")
    p.add_argument("table")
    p.add_argument("--json", action="store_true")
    p = add("search", cmd_search, "find tables by business meaning")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p = add("describe", cmd_describe, "keys plus business context for a table")
    p.add_argument("table")
    p.add_argument("--json", action="store_true")
    p = add("context", cmd_context, "import / add / export business context")
    p.add_argument("action", choices=["import", "import-notes", "add", "export", "stats"])
    p.add_argument("path", nargs="?")
    p.add_argument("--verified", action="store_true")
    p.add_argument("--schema-key", default="schema", help="front-matter key holding the schema name (import-notes)")
    p.add_argument("--default-schema", default="main")
    p.add_argument("--out")
    p.add_argument("--kind", choices=sorted(["concept", "relation", "link", "column", "metric", "table"]))
    p.add_argument("fields", nargs="*", help="for add: key=value pairs (codes=1:new,2:paid; link=table:col:role)")
    p = add("fetch", cmd_fetch, "fetch STATISTICS / KEY_COLUMN_USAGE through an external read-only command")
    p.add_argument("--cmd", required=True, help="command template with {sql}, printing JSON rows")
    p.add_argument("--schema-expr", default="DATABASE()")
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--out", required=True)
    add("ui", cmd_ui, "open keygraph.html in the browser")
    a = ap.parse_args(argv)
    if a.cmd == "context" and a.action == "add" and not a.kind:
        ap.error("context add needs --kind")
    if a.cmd == "context" and a.action == "add" and a.path:
        a.fields = [a.path] + a.fields
    try:
        a.fn(a)
    except (AmbiguousTable, UnknownTable, ValueError) as exc:
        print(f"keygraph: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
