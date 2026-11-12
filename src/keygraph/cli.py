"""keygraph command line."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from datetime import UTC
from pathlib import Path

from . import query as Q
from . import search as S
from .areas import apply_curation, area_links, cluster_areas
from .code_links import merge_code_joins
from .context import ContextDB, read_copy_globs
from .docs_out import write_docs
from .explorer import build_html
from .graph import BUILD_FILE, AmbiguousTable, Graph, UnknownTable, build_graph
from .hubs import detect_hubs, mark_hubs
from .infer import Options
from .mirrors import detect_mirrors, mark_mirrors, transfer_mirror_edges
from .model import Schema
from .overrides import Overrides
from .report import write_report
from .summary import relation_summary

CUR_PREFIX = "curation: "
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


def load_graph(dir) -> Graph:
    path = Path(dir) / "graph.json"
    if not path.exists():
        raise SystemExit(f"no graph at {path}; run `keygraph scan` or `keygraph load` first")
    return Graph.load(path)


def _load(args) -> Graph:
    return load_graph(args.dir)


def _ctx(args, g: Graph) -> ContextDB:
    return ContextDB(Path(args.dir) / "keygraph.db", g)


MANIFEST = ".keygraph-pages.json"


def _drop_stale_pages(out: Path, written: list[str]) -> None:
    """Delete only the pages the previous build wrote and this one did not (areas can be renamed).

    The previous build's page list is kept in a manifest. Only *.md files directly inside a real
    (non-symlink) areas/ or tables/ directory are ever removed, so a user's own files survive.
    """
    mf = out / MANIFEST
    try:
        old = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else []
    except (OSError, ValueError):
        old = []
    old = [x for x in old if isinstance(x, str)] if isinstance(old, list) else []
    keep = set(written)
    for rel in old:
        parts = rel.split("/")
        if rel in keep or len(parts) != 2 or parts[0] not in ("areas", "tables") or not rel.endswith(".md"):
            continue
        d = out / parts[0]
        if d.is_symlink() or not d.is_dir():
            continue
        f = d / parts[1]
        if f.is_file() and not f.is_symlink():
            f.unlink()
    mf.write_text(json.dumps(sorted(keep)), encoding="utf-8")


def _code_summary(ctx: ContextDB | None, snippets: bool) -> dict:
    if not ctx:
        return {}
    from .code.store import CodeStore
    with CodeStore(ctx.path, readonly=True) as st:
        return st.summary(snippets=snippets)


def _query_summary(ctx: ContextDB | None) -> dict:
    if not ctx:
        return {}
    from .queries.store import QueryStore
    with QueryStore(ctx.path, readonly=True) as st:
        return st.summary()


def render(out: Path, g: Graph, ctx: ContextDB | None, snippets: bool | None = None, queries: bool | None = None) -> dict:
    """Write graph.json, RELATIONS_REPORT.md and keygraph.html. snippets / queries: put code snippets and the query
    log into the explorer and docs; None keeps the choice an earlier build stored in graph.json (render_options)."""
    out.mkdir(parents=True, exist_ok=True)
    opts = dict(g.data.get("render_options") or {})
    if snippets is not None:
        opts["snippets"] = bool(snippets)
    if queries is not None:
        opts["queries"] = bool(queries)
    g.data["render_options"] = {k: opts[k] for k in sorted(opts)}
    code = _code_summary(ctx, opts.get("snippets", False))
    qlog = _query_summary(ctx) if opts.get("queries") else {}
    cur = ctx.curation() if ctx else {"areas": [], "hubs": {}, "mirrors": {}}
    warns: list[str] = list(cur.get("warnings", []))
    mcur = cur.get("mirrors") or {}
    schemas = {n["schema"] for n in g.nodes.values()}
    for m, o in mcur.items():
        if o in (False, None) and m not in schemas:
            warns.append(f"mirrors.yaml: '{m}: {o}' ignored: unknown schema")
    g.data["copy_schemas"] = sorted(dict.fromkeys(cur.get("copies") or []))
    mark_mirrors(g, detect_mirrors(g, force={m: o for m, o in mcur.items() if isinstance(o, str)},
                                   never={m for m, o in mcur.items() if o in (False, None)}, warnings=warns))
    transfer_mirror_edges(g)  # relations of the original tables, copied onto their mirror copies
    names: dict[str, list[str]] = {}
    for k in ("include", "exclude"):
        names[k] = []
        for t in cur["hubs"].get(k, []):
            try:
                g.resolve(t)
                names[k].append(t)
            except ValueError as exc:
                warns.append(f"hubs {k} '{t}' ignored: {exc}")
    if ctx:
        merge_code_joins(g, ctx.path)  # joins seen in code and logged queries; after mirrors, before hubs and areas
    mark_hubs(g, detect_hubs(g, include=names["include"], exclude=names["exclude"]))
    areas, cwarns = apply_curation(g, cluster_areas(g), cur["areas"])
    g.data["areas"], g.data["area_links"] = areas, area_links(g, areas)
    # a re-render reloads graph.json, so drop curation warnings from the previous render first
    g.data["warnings"] = [w for w in g.data.get("warnings", []) if not w.startswith(CUR_PREFIX)]
    g.data["warnings"] += [CUR_PREFIX + w for w in warns + cwarns]
    dump = ctx.dump() if ctx else None  # one dump for the docs and the HTML (write_docs only reads it)
    docs = write_docs(out, g, areas, dump, code=code, queries=qlog)
    _drop_stale_pages(out, docs["files"])
    g.save(out / "graph.json")
    (out / "RELATIONS_REPORT.md").write_text(write_report(g, ctx), encoding="utf-8")
    html = build_html(g, dump, code=code, queries=qlog)
    (out / "keygraph.html").write_text(html, encoding="utf-8")
    return {"graph.json": (out / "graph.json").stat().st_size, "RELATIONS_REPORT.md": (out / "RELATIONS_REPORT.md").stat().st_size,
            "keygraph.html": len(html.encode("utf-8"))}


def url_source(url: str) -> dict:
    """Describe a database URL as {"kind", "database"}: never the host, user, password, port or query.
    The database name is kept only when it is a plain name; anything odd is left out rather than risk a leak."""
    kind = url.split(":", 1)[0].split("+", 1)[0].lower()
    db = ""
    try:
        from sqlalchemy.engine.url import make_url
        db = make_url(url).database or ""
    except Exception:  # noqa: BLE001  (no SQLAlchemy, or a URL it cannot parse): parse by hand
        rest = url.split("://", 1)[1] if "://" in url else ""
        rest = rest.split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1]
        db = rest.partition("/")[2] if kind != "sqlite" else rest
    if kind == "sqlite":
        db = re.split(r"[\\/]", db)[-1]
    else:
        db = db.split("/")[0]
    return {"kind": kind, "database": db} if db and re.fullmatch(r"[\w.$-]+", db) else {"kind": kind}


def _build_time() -> float:
    """Now, or SOURCE_DATE_EPOCH when it is a plain number of seconds (reproducible builds)."""
    import os
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    if sde is None or sde == "":
        return time.time()
    if re.fullmatch(r"[0-9]{1,11}", sde):
        return float(int(sde))
    print("keygraph: ignoring SOURCE_DATE_EPOCH (not a plain number of seconds); using the current time", file=sys.stderr)
    return time.time()


def write_build_info(out: Path, g: Graph, sources: list[dict] | None) -> None:
    """build.json: when and from what this map was built. The only file that differs between identical builds.
    Written (atomically) before graph.json, so a graph on disk never pairs with an older build.json."""
    import os
    from datetime import datetime

    from . import __version__
    ts = datetime.fromtimestamp(_build_time(), UTC)
    info = {"built_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "keygraph_version": __version__,
            "sources": list(sources or []), "schemas": sorted({n["schema"] for n in g.nodes.values()})}
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / (BUILD_FILE + ".tmp")
    tmp.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, out / BUILD_FILE)
    g.build = info


def run_pipeline(schema: Schema, out: str | Path, overrides: str | None = None, context: list[str] | None = None,
                 notes: list[dict] | None = None, options: Options | None = None, verbose=True, snippets: bool = False,
                 queries: bool = False, copy_schemas: list[str] | None = None, sources: list[dict] | None = None) -> dict:
    out = Path(out)
    t0 = time.time()
    ov = Overrides.load(overrides) if overrides else None
    options = options or Options()
    globs = list(dict.fromkeys(list(copy_schemas or []) + read_copy_globs(context)))
    options.copy_schemas.extend(x for x in globs if x not in options.copy_schemas)  # inference runs before context import
    g = build_graph(schema, ov, options)
    ctx = ContextDB(out / "keygraph.db", g)
    ctx.index_graph(g)
    if context:
        ctx.clear_curation()  # rules from files that are no longer imported must not apply
    for path in context or []:
        ctx.import_path(path)
    if copy_schemas:
        ctx.add_copy_schemas(copy_schemas)
    ctx.reindex()
    prof = ctx.load_profiles()
    if prof:
        g.data["profiles"] = prof
    S.apply_context_to_graph(g, ctx)
    write_build_info(out, g, sources)
    sizes = render(out, g, ctx, snippets=snippets, queries=queries)
    result = {"stats": g.data["stats"], "context": ctx.counts(), "files": sizes, "seconds": round(time.time() - t0, 1),
              "warnings": g.data["warnings"][:10] + ctx.warnings[:10]}
    ctx.close()
    if verbose:
        st = result["stats"]
        print(f"keygraph: {st['tables']} tables, {st['columns']} columns, {st['indexes']} indexes, {st['edges']} edges "
              f"{st['edges_by_kind']} -> {out}/ ({result['seconds']}s)")
    return result


def cmd_scan(a):
    from .adapters.live import scan_any
    sch, via = scan_any(a.url, a.schema, a.all_schemas, getattr(a, "generic", False))
    src = {**url_source(a.url), "kind": via}
    run_pipeline(sch, a.dir, a.overrides, a.context, snippets=a.snippets, queries=a.queries,
                 copy_schemas=getattr(a, "copy_schema", None), sources=[src])


def cmd_load(a):
    from .adapters.dump import load_dump
    filt = None
    if a.where:
        pairs = [w.split("=", 1) for w in a.where]
        filt = lambda r: all(str(r.get(k)) == v for k, v in pairs)
    cm = _json_arg(a.columns_map)
    if a.flatten:
        cm["_flatten"] = a.flatten
    sch = load_dump(a.columns, a.statistics, a.key_columns, a.default_schema, cm, _json_arg(a.statistics_map),
                    _json_arg(a.kcu_map), filt, tables=a.tables)
    names = [Path(str(x)).name for x in (a.columns, a.statistics, a.key_columns, a.tables) if x]
    run_pipeline(sch, a.dir, a.overrides, a.context, snippets=a.snippets, queries=a.queries,
                 copy_schemas=getattr(a, "copy_schema", None), sources=[{"kind": "dump", "files": names}])


def cmd_render(a):
    g = _load(a)
    ctx = _ctx(a, g)
    ctx.index_graph(g)
    ctx.reindex()
    g.data["profiles"] = ctx.load_profiles() or g.data.get("profiles", {})
    print(json.dumps(render(Path(a.dir), g, ctx, snippets=a.snippets or None, queries=a.queries or None)))
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


def cmd_profile(a):
    from .adapters import open_source
    from .adapters.fetch import command_runner
    from .profile import run_profile
    g = _load(a)
    ctx = _ctx(a, g)
    if a.source in ("mongo", "mongodb"):
        from pymongo import MongoClient  # optional extra
        src = open_source("mongodb", db=MongoClient(a.url)[a.db])
    elif a.cmd:
        src = open_source(a.source, run=command_runner(a.cmd, a.timeout))
    else:
        src = open_source(a.source, url=a.url)
    src.bare = a.bare_tables
    rep = run_profile(g, src, ctx, tables=[t for t in (a.tables or "").split(",") if t], top_hubs=a.top_hubs, with_context=a.with_context,
                      total_budget_s=a.total_budget, n=a.sample, strategy=a.strategy, budget_s=a.budget,
                      skip_over_rows=a.skip_over_rows, force=a.force)
    ctx.reindex()
    render(Path(a.dir), g, ctx)
    ctx.close()
    print(json.dumps(rep, indent=1, ensure_ascii=False))


def path_result(g: Graph, a: str, b: str, max_hops=4, min_confidence=0.6, k=3, include_mirrors=False) -> dict:
    ps = Q.find_paths(g, a, b, max_hops, min_confidence, k, include_mirrors=include_mirrors)
    return {"from": g.resolve(a), "to": g.resolve(b), "paths": ps}


def describe_result(dir, g: Graph, table: str, readonly: bool = False, brief: bool = False, min_confidence: float = 0.6) -> dict:
    with ContextDB(Path(dir) / "keygraph.db", g, readonly) as ctx:
        r = S.describe(g, ctx, table, brief, min_confidence)
    if brief:
        return r
    r["relation_summary"] = relation_summary(g, table, g.data.get("areas", []), min_confidence)
    return r


def area_result(dir, g: Graph, name: str, include_mirrors=False, readonly: bool = False) -> dict:
    with ContextDB(Path(dir) / "keygraph.db", g, readonly) as ctx:
        grains = {r["table_id"]: r["grain"] for r in ctx.rows("table_notes") if r.get("grain")}
    return Q.area_detail(g, name, grains, include_mirrors=include_mirrors)


def cmd_path(a):
    g = _load(a)
    r = path_result(g, a.a, a.b, a.max_hops, a.min_confidence, a.k, a.include_mirrors)
    _emit(a, r, Q.format_paths(a.a, a.b, r["paths"]))


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
    with _ctx(a, g) as ctx:
        r = S.search(g, ctx, " ".join(a.query), a.limit, with_paths=not a.no_paths)
    _emit(a, r, S.format_search(r))


def cmd_describe(a):
    g = _load(a)
    r = describe_result(a.dir, g, a.table, brief=a.brief, min_confidence=a.min_confidence)
    _emit(a, r, S.format_describe(r) + ("" if a.brief else "\n" + Q.format_relation_summary(r["relation_summary"])))


def cmd_areas(a):
    g = _load(a)
    rows = Q.list_areas(g, include_mirrors=a.include_mirrors)
    _emit(a, rows, Q.format_areas(rows))


def cmd_area(a):
    g = _load(a)
    r = area_result(a.dir, g, a.name, a.include_mirrors)
    _emit(a, r, Q.format_area(r))


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
    if a.action == "history":  # read-only: no graph needed, the database is never created or changed
        with ContextDB(Path(a.dir) / "keygraph.db", readonly=True) as ctx:
            rows = ctx.history(a.limit, a.ref, a.kind)
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True))
        else:
            for r in rows:
                print(f"{r['at']}  {r['action']:<8} {r['kind']} {r['ref']}  by {r['source']}")
            if not rows:
                print("no history")
        return
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
        counts = {"tables": 0, "columns": 0}
        for kind, key in (("table", "tables"), ("column", "columns")):
            for item in data[key]:
                if ctx.upsert(kind, item, item.get("source", ""), item.get("verified", False)):
                    counts[key] += 1
        ctx.reindex()
    elif a.action == "add":
        d = _kv(a.fields)
        who = d.get("source") or a.author or os.environ.get("KEYGRAPH_AUTHOR") or "context-add"
        ok = ctx.upsert(a.kind, d, who, a.verified or d.get("verified") in ("true", "yes", "1"), merge=True)
        ctx.reindex()
        counts = {a.kind: 1 if ok else 0}
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
    print(json.dumps({"imported": counts, "kept": ctx.kept, "warnings": ctx.warnings}, ensure_ascii=False, indent=1))
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


def cmd_mcp(a):
    try:
        from .mcp_server import build_server
    except ImportError:
        raise SystemExit("the MCP server needs the mcp extra: pip install 'keygraph[mcp]' (or uvx --from 'keygraph[mcp]' keygraph mcp)")
    build_server(a.dir).run("stdio")


def cmd_doctor(a):
    from . import doctor as D
    r = D.run(a.dir)
    if a.report:
        Path(a.report).write_text(D.report(r), encoding="utf-8")
        print(f"wrote {a.report}: environment block for a bug report (no table names, SQL or values)", file=sys.stderr)
    _emit(a, r, D.format_text(r))
    if r["status"] == "fail":
        raise SystemExit(1)


def cmd_dev(a):
    """keygraph dev on|off|status|clear|note. None of these needs a graph; only `note` writes an event."""
    from . import devlog
    out, d = Path(a.dir), devlog.dev_dir(a.dir)
    if a.dev_cmd == "on":
        d.mkdir(parents=True, exist_ok=True)
        (d / "enabled").write_text("", encoding="utf-8")
        print(f"dev mode on: logging to {d} (KEYGRAPH_DEV=0 overrides)")
    elif a.dev_cmd == "off":
        (d / "enabled").unlink(missing_ok=True)
        print("dev mode off (the log is kept; `keygraph dev clear` deletes it)")
    elif a.dev_cmd == "status":
        st = devlog.status(out)
        _emit(a, st, f"dev mode {'on' if st['enabled'] else 'off'} ({st['source']}); {st['events_bytes']} bytes of events, "
                     f"{st['trace_files']} trace files in {st['dir']}")
    elif a.dev_cmd == "clear":
        devlog.clear(out, a.all)
        print("cleared the dev log" + (" and switched dev mode off" if a.all else " (dev mode setting kept)"))
    else:
        q = devlog.mask_text(" ".join(a.text))
        sess, on = devlog.session_id("cli"), devlog.enabled(out)
        if on:
            devlog.Recorder(out, "cli", session=sess).record("note", "dev_note", {"question": q})
        _emit(a, {"noted": on, "session": sess, "question": q},
              f"noted: {q}" if on else "dev mode is off, so nothing was recorded (turn it on: keygraph dev on)")


def _code_db(a) -> Path:
    load_graph(a.dir)  # a clear message when nothing was built
    return Path(a.dir) / "keygraph.db"


def _split_path_line(ref: str) -> tuple[str, int | None]:
    head, sep, tail = ref.rpartition(":")
    return (head, int(tail)) if sep and tail.isdigit() and head else (ref, None)


def cmd_code(a):
    from . import code as C
    from .code import text as T
    db = _code_db(a)
    if a.code_cmd == "index":
        if a.name and len(a.name) != len(a.repos):
            raise ValueError(f"give one --name per repo ({len(a.repos)} repos, {len(a.name)} names)")
        names = a.name or [None] * len(a.repos)
        res = [C.index_repo(db, r, n, a.patterns or [], a.exclude or [], a.full, a.max_file_kb, a.max_uses, a.dialect,
                          copy_schemas=a.copy_schema or ())
               for r, n in zip(a.repos, names)]
        g = _load(a)
        ctx = _ctx(a, g)
        if a.copy_schema:
            ctx.add_copy_schemas(list(dict.fromkeys(a.copy_schema)))
        render(Path(a.dir), g, ctx, snippets=a.snippets or None)
        ctx.close()
        _emit(a, {"repos": res}, "\n".join(T.format_index(r) for r in res))
    elif a.code_cmd == "where":
        r = C.where(db, a.ref, a.kind, a.repo, a.min_confidence, a.limit)
        _emit(a, r, T.format_where(r))
    elif a.code_cmd == "what":
        if not a.path and not a.symbol:
            raise ValueError("give PATH[:LINE] or --symbol NAME")
        path, line = _split_path_line(a.path) if a.path else (None, None)
        r = C.what(db, path, line, a.symbol, a.repo)
        _emit(a, r, T.format_what(r))
    else:
        r = C.stats(db)
        _emit(a, r, T.format_stats(r))


def cmd_queries(a):
    from . import queries as QL
    from .queries import text as T
    db = Path(a.dir) / "keygraph.db"
    if a.q_cmd == "add":
        sql = sys.stdin.read() if a.sql == "-" else Path(a.sql).read_text(encoding="utf-8")
        r = QL.add(db, sql, a.source, a.ms, a.rows, a.context or "", a.tags or "", a.by or "", a.at, a.error,
                   a.keep_literals, a.dialect)
        _emit(a, r, f"logged run {r['run']} as {r['fp']} ({', '.join(r['tables']) or 'no known tables'})"
                    + (f"; unresolved: {', '.join(r['unresolved'])}" if r["unresolved"] else ""))
    elif a.q_cmd == "import":
        r = QL.import_jsonl(db, a.file, a.keep_literals, a.dialect)
        _emit(a, r, f"imported {r['added']} runs" + "".join(f"\n  line {e['line']}: {e['error']}" for e in r["errors"]))
    elif a.q_cmd == "top":
        r = QL.top(db, a.by, a.table, a.limit)
        _emit(a, r, T.format_list(f"top queries by {a.by}" + (f" touching {r['table']}" if r["table"] else ""), r["queries"]))
    elif a.q_cmd == "slow":
        r = QL.slow(db, a.limit, a.min_runs)
        _emit(a, r, T.format_list("slowest queries by median time", r["queries"]))
    elif a.q_cmd == "for":
        r = QL.for_ref(db, a.ref)
        _emit(a, r, T.format_list(f"queries touching {r['table']}" + (f".{r['column']}" if r["column"] else ""), r["queries"]))
    elif a.q_cmd == "search":
        r = QL.search(db, " ".join(a.text), a.limit)
        _emit(a, r, T.format_list(f"queries matching '{r['query']}'", r["queries"]))
    elif a.q_cmd == "show":
        r = QL.show(db, a.fp)
        _emit(a, r, T.format_show(r))
    else:
        r = QL.stats(db)
        _emit(a, r, T.format_stats(r))


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
        p.add_argument("--copy-schema", action="append", metavar="GLOB",
                       help="glob of schemas that are copies of another database, e.g. 'ods_*' (repeatable)")
        render_opts(p)

    def render_opts(p):
        p.add_argument("--snippets", action="store_true", help="include code snippets in keygraph.html (default: locations only)")
        p.add_argument("--queries", action="store_true", help="include the query log in keygraph.html and the table pages")

    p = add("scan", cmd_scan, "scan a live database (metadata only; MySQL, Postgres, SQL Server and ClickHouse use their own adapter)")
    p.add_argument("--url", required=True)
    p.add_argument("--schema", action="append")
    p.add_argument("--all-schemas", action="store_true")
    p.add_argument("--generic", action="store_true", help="always use the SQLAlchemy inspector, not the engine's own adapter")
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
    p.add_argument("--tables", help="information_schema.TABLES dump (engine, rows, sizes, comment)")
    p.add_argument("--where", action="append", help="keep only COLUMNS rows with field=value (repeatable)")
    build_opts(p)
    render_opts(add("render", cmd_render, "rewrite report and explorer from graph.json + keygraph.db"))
    p = add("probe", cmd_probe, "sample value overlap for inferred edges (SELECT only)")
    p.add_argument("--url", required=True)
    p.add_argument("--sample", type=int, default=500)
    p.add_argument("--max-edges", type=int, default=200)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p = add("profile", cmd_profile, "sample column values (read-only, bounded) and store value insights")
    p.add_argument("--source", required=True, choices=["mysql", "mariadb", "postgres", "sqlite", "sqlserver", "clickhouse", "mongodb"])
    p.add_argument("--url", help="SQLAlchemy URL (or Mongo URI)")
    p.add_argument("--cmd", help="external read-only command template with {sql}, printing JSON rows")
    p.add_argument("--db", help="Mongo database name")
    p.add_argument("--tables", help="comma-separated tables")
    p.add_argument("--top-hubs", type=int, default=0, help="also the N most-connected tables")
    p.add_argument("--with-context", action="store_true", help="also every table that has context notes")
    p.add_argument("--sample", type=int, default=10000)
    p.add_argument("--strategy", default="auto", choices=["auto", "limit", "recent", "tablesample"])
    p.add_argument("--budget", type=float, default=20.0, help="seconds per table; slower tables are flagged")
    p.add_argument("--total-budget", type=float, default=600.0)
    p.add_argument("--skip-over-rows", type=int, default=50_000_000)
    p.add_argument("--force", action="store_true", help="profile tables over the row limit")
    p.add_argument("--bare-tables", action="store_true", help="use bare table names (graph schema is only a label)")
    p.add_argument("--timeout", type=int, default=180)
    p = add("path", cmd_path, "best join paths between two tables")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--max-hops", type=int, default=4)
    p.add_argument("--min-confidence", type=float, default=0.6)
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.add_argument("--include-mirrors", action="store_true", help="also use tables in mirror schemas (copies of another schema)")
    p = add("neighbors", cmd_neighbors, "tables related to a table")
    p.add_argument("table")
    p.add_argument("--depth", type=int, default=1)
    p.add_argument("--min-confidence", type=float, default=0.6, help="hide weaker relations and count them (0 shows all)")
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
    p.add_argument("--no-paths", action="store_true", help="skip the suggested join paths between the top hits")
    p.add_argument("--json", action="store_true")
    p = add("describe", cmd_describe, "keys plus business context for a table")
    p.add_argument("table")
    p.add_argument("--brief", action="store_true", help="keys, filter columns, notes and columns as name/type/key/codes only")
    p.add_argument("--min-confidence", type=float, default=0.6, help="hide weaker relations and count them (0 shows all)")
    p.add_argument("--json", action="store_true")
    p = add("areas", cmd_areas, "list the business areas (clusters of related tables)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--include-mirrors", action="store_true", help="also use tables in mirror schemas (copies of another schema)")
    p = add("area", cmd_area, "one area: tables with grain, internal relations, neighbouring areas, hub labels")
    p.add_argument("name", help="area id, area name (any case) or a table")
    p.add_argument("--json", action="store_true")
    p.add_argument("--include-mirrors", action="store_true", help="also use tables in mirror schemas (copies of another schema)")
    p = add("context", cmd_context, "import / add / export business context")
    p.add_argument("action", choices=["import", "import-notes", "add", "export", "stats", "history"])
    p.add_argument("path", nargs="?")
    p.add_argument("--verified", action="store_true")
    p.add_argument("--schema-key", default="schema", help="front-matter key holding the schema name (import-notes)")
    p.add_argument("--default-schema", default="main")
    p.add_argument("--out")
    p.add_argument("--kind", choices=sorted(["concept", "relation", "link", "column", "metric", "table"]))
    p.add_argument("--author", help="for add: who is writing (the source label unless source=... is given; env KEYGRAPH_AUTHOR)")
    p.add_argument("--ref", help="for history: only this item (table, table.column, concept or metric name)")
    p.add_argument("--limit", type=int, default=50, help="for history: newest N entries")
    p.add_argument("--json", action="store_true", help="for history: JSON output")
    p.add_argument("fields", nargs="*", help="for add: key=value pairs (codes=1:new,2:paid; link=table:col:role)")
    p = add("fetch", cmd_fetch, "fetch STATISTICS / KEY_COLUMN_USAGE through an external read-only command")
    p.add_argument("--cmd", required=True, help="command template with {sql}, printing JSON rows")
    p.add_argument("--schema-expr", default="DATABASE()")
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--out", required=True)
    add("mcp", cmd_mcp, "serve keygraph to AI agents over MCP (stdio); needs keygraph[mcp]")
    add("ui", cmd_ui, "open keygraph.html in the browser")
    p = add("doctor", cmd_doctor, "check the install and a built directory; print the MCP launch command")
    p.add_argument("--json", action="store_true")
    p.add_argument("--report", metavar="FILE", help="also write a redacted environment block for a bug report")
    p = sub.add_parser("dev", help="dev mode: log how agents use keygraph, locally (on, off, status, clear, note)")
    p.set_defaults(fn=cmd_dev)
    ds = p.add_subparsers(dest="dev_cmd", required=True)

    def dev_cmd(name, help_):
        q = ds.add_parser(name, help=help_)
        _dir_args(q)
        return q

    dev_cmd("on", "start logging to <dir>/dev (or set KEYGRAPH_DEV=1)")
    dev_cmd("off", "stop logging (keeps the log)")
    dev_cmd("status", "is dev mode on, and how big is the log").add_argument("--json", action="store_true")
    dev_cmd("clear", "delete the logged events and traces").add_argument("--all", action="store_true", help="also remove the on marker")
    q = dev_cmd("note", "record the question you are about to answer (only when dev mode is on)")
    q.add_argument("text", nargs="+")
    q.add_argument("--json", action="store_true")
    dialects = sorted({"mysql", "mariadb", "postgres", "postgresql", "tsql", "sqlserver", "clickhouse", "sqlite"})
    pc = sub.add_parser("code", help="link source code to tables and columns (index, where, what, stats)")
    pc.set_defaults(fn=cmd_code)
    cs = pc.add_subparsers(dest="code_cmd", required=True)

    def code_cmd(name, help_):
        p = cs.add_parser(name, help=help_)
        p.add_argument("--dir", "--out", dest="dir", default=DEFAULT_DIR, help="built keygraph output directory")
        p.add_argument("--json", action="store_true")
        return p

    p = code_cmd("index", "index one or more repos (read as text, never executed)")
    p.add_argument("repos", nargs="+", metavar="REPO")
    p.add_argument("--name", action="append", help="short repo name, one per REPO in order (default: the folder name)")
    p.add_argument("--patterns", action="append", help="code-patterns YAML/JSON file (repeatable); <dir>/code-patterns.yaml is always read")
    p.add_argument("--exclude", action="append", help="glob of paths to skip (repeatable)")
    p.add_argument("--full", action="store_true", help="re-read every file instead of only changed ones")
    p.add_argument("--max-file-kb", type=int, default=1024)
    p.add_argument("--max-uses", type=int, default=500, help="use refs kept per column per repo")
    p.add_argument("--dialect", choices=dialects, help="SQL dialect for parsing (with keygraph[code])")
    p.add_argument("--snippets", action="store_true", help="also show code snippets in keygraph.html")
    p.add_argument("--copy-schema", action="append", metavar="GLOB",
                   help="glob of schemas that are copies, e.g. 'ods_*': bare names prefer other tables (repeatable)")
    p = code_cmd("where", "data -> code: where a table or column is declared, written, read and used")
    p.add_argument("ref", metavar="TABLE[.COLUMN]")
    p.add_argument("--kind", choices=["declare_table", "declare_column", "sql_write", "sql_read", "use"])
    p.add_argument("--repo")
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--limit", type=int)
    p = code_cmd("what", "code -> data: the tables and columns a file, line or symbol touches")
    p.add_argument("path", nargs="?", metavar="PATH[:LINE]")
    p.add_argument("--symbol")
    p.add_argument("--repo")
    code_cmd("stats", "per repo: files, refs by kind, unresolved names, top tables")
    pq = sub.add_parser("queries", help="query log: SQL that was run, its timing and business question (add, import, top, ...)")
    pq.set_defaults(fn=cmd_queries)
    qs = pq.add_subparsers(dest="q_cmd", required=True)

    def q_cmd(name, help_):
        p = qs.add_parser(name, help=help_)
        p.add_argument("--dir", "--out", dest="dir", default=DEFAULT_DIR, help="built keygraph output directory")
        p.add_argument("--json", action="store_true")
        return p

    p = q_cmd("add", "log one query run")
    p.add_argument("--sql", required=True, help="file holding the SQL, or - for stdin")
    p.add_argument("--source", required=True, help="where it ran, e.g. mysql:shop or warehouse")
    p.add_argument("--ms", type=float, help="run time in milliseconds")
    p.add_argument("--rows", type=int, help="rows returned or changed")
    p.add_argument("--context", help="the business question the query answered")
    p.add_argument("--tags", help="comma-separated tags")
    p.add_argument("--by", help="who ran it")
    p.add_argument("--at", help="ISO time it ran (default now)")
    p.add_argument("--error", help="error text when the run failed")
    p.add_argument("--keep-literals", action="store_true", help="store literal values (secret-looking values stay masked)")
    p.add_argument("--dialect", choices=dialects)
    p = q_cmd("import", "log many runs from a JSONL file (sql, source, ms, rows, context, tags, by, at, error)")
    p.add_argument("file")
    p.add_argument("--keep-literals", action="store_true")
    p.add_argument("--dialect", choices=dialects)
    p = q_cmd("top", "most used queries")
    p.add_argument("--by", choices=["runs", "recent", "people"], default="runs")
    p.add_argument("--table")
    p.add_argument("--limit", type=int, default=10)
    p = q_cmd("slow", "slowest queries: median and p95 ms, and the recent trend")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--min-runs", type=int, default=1)
    p = q_cmd("for", "queries that touch a table or column")
    p.add_argument("ref", metavar="TABLE[.COLUMN]")
    p = q_cmd("search", "full-text search over business context and SQL")
    p.add_argument("text", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p = q_cmd("show", "one query fingerprint with its runs")
    p.add_argument("fp")
    q_cmd("stats", "runs, distinct queries, sources and unresolved tables")
    a, extra = ap.parse_known_args(argv)
    if extra:
        # Python 3.11 argparse stops filling `path`/`fields` once an option (--kind) appears between
        # positionals, so the trailing key=value pairs arrive here. Put them back where 3.12+ puts them.
        if a.cmd != "context" or any(x.startswith("-") for x in extra):
            ap.error("unrecognized arguments: " + " ".join(extra))
        if a.path is None:
            a.path, extra = extra[0], extra[1:]
        a.fields = list(a.fields or []) + extra
    if a.cmd == "context" and a.action == "add" and not a.kind:
        ap.error("context add needs --kind")
    if a.cmd == "context" and a.action == "add" and a.path:
        a.fields = [a.path] + a.fields
    try:
        _run(a)
    except (AmbiguousTable, UnknownTable, ValueError) as exc:
        print(f"keygraph: {exc}", file=sys.stderr)
        return 2
    return 0


class _Tee:
    """Forwards stdout and keeps its hash, size and (up to 16 KB) text, for the dev log."""

    def __init__(self, real):
        import hashlib
        self.real, self.h, self.bytes, self.chars, self.parts = real, hashlib.sha256(), 0, 0, []

    def write(self, s):
        try:
            b = s.encode("utf-8", "replace")
            self.h.update(b)
            self.bytes += len(b)
            self.chars += len(s)
            if self.bytes <= 16 * 1024 + len(b):
                self.parts.append(s)
        except Exception as exc:  # noqa: BLE001  (logging never changes output)
            from . import devlog
            devlog._warn(exc)
        return self.real.write(s)

    def __getattr__(self, name):
        return getattr(self.real, name)


_TEXT_PROFILE = {"describe", "profile", "search"}  # text output may carry sampled profile values: hash only


def _command_name(a) -> str:
    return " ".join(x for x in (a.cmd, getattr(a, "code_cmd", None), getattr(a, "q_cmd", None)) if x)


def _run(a) -> None:
    """Run the command; with dev mode on, also record it (masked args, timing, exit status, stdout hash)."""
    from . import devlog
    d = getattr(a, "dir", None)
    if a.cmd == "dev" or d is None or not devlog.enabled(d):
        a.fn(a)
        return
    name = _command_name(a)
    args = {k: v for k, v in vars(a).items() if k not in ("fn", "dir", "cmd", "code_cmd", "q_cmd")}
    real, tee, t0, ok, err, status = sys.stdout, None, time.perf_counter(), True, None, 0
    try:
        tee = sys.stdout = _Tee(real)
        a.fn(a)
    except BaseException as exc:
        ok = False
        err = {"type": type(exc).__name__, "message": str(exc)}
        status = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) else \
            0 if isinstance(exc, SystemExit) and not exc.code else 2 if isinstance(exc, (AmbiguousTable, UnknownTable, ValueError)) else 1
        ok = status == 0
        if ok:
            err = None
        raise
    finally:
        sys.stdout = real
        try:
            text = "".join(tee.parts) if tee else ""
            resp = devlog.stdout_fields(name, text, tee.bytes, tee.h.hexdigest(), tee.chars // 4, a.cmd not in _TEXT_PROFILE) if tee else {}
            args["exit_status"] = status
            devlog.Recorder(d, "cli").record("command", name, args,
                                             (time.perf_counter() - t0) * 1000, ok, err, resp)
        except Exception as exc:  # noqa: BLE001
            devlog._warn(exc)


if __name__ == "__main__":
    raise SystemExit(main())
