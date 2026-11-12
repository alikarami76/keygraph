"""MCP server: keygraph's read-only queries as tools for any MCP client.

Each tool returns the same JSON as the matching `keygraph <command> --json`, except `areas`, which wraps the
CLI's list as {"areas": [...]} (MCP tools return objects). Nothing here runs SQL or
touches a database: it reads graph.json and keygraph.db from a built output directory.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from . import code as CI
from . import queries as QL
from . import query as Q
from . import search as S
from .cli import area_result, describe_result, load_graph, path_result
from .context import ContextDB
from .docs_out import table_file
from .graph import Graph

INSTRUCTIONS = """keygraph maps the keys of a database and the relations between its tables. It is deterministic and
answers from a prebuilt graph; it never queries the database. Every tool is read-only except `query_submit`.
Start with `search` (find tables by business meaning) or `areas`, then `describe` a table (`brief=true` for a cheap first look at a wide table), and use `path` before writing
any multi-table JOIN. Relations are either declared foreign keys (confidence 1.0) or inferred from names and key shapes;
inferred relations carry a confidence and the evidence behind it, so check anything below about 0.8 (and every fan-out
hop, where one row becomes many) with a small SELECT. Value profiles are sampled, may be stale, and never contain raw
values for PII columns. Table names resolve leniently, but when a bare name exists in several schemas, give schema.table.
When code was indexed, `code_where` lists where a table or column is declared, written, read and used in code, and
`code_what` lists the tables and columns a file, line or symbol touches. The query log answers how a table was
queried before: `query_for`, `query_top` and `query_search`. `query_submit` is the only tool that writes: it logs a
query you ran (SQL, source, timing and the business question) into the query tables and nothing else."""


MAX_DEPTH = 3  # neighbors beyond 3 hops returns most of a large graph


def build_server(dir: str | Path) -> MCPServer:
    """Create the server for a built output directory (does not start it)."""
    out = Path(dir)
    load_graph(out)  # fail early with a clear message when nothing was built
    cache: dict = {}

    def graph() -> Graph:
        """The graph, reloaded only when graph.json changed (the directory may be rebuilt while we serve)."""
        bj = out / "build.json"
        stamp = ((out / "graph.json").stat().st_mtime_ns, bj.stat().st_mtime_ns if bj.exists() else 0)
        if cache.get("stamp") != stamp:
            cache["stamp"], cache["g"] = stamp, load_graph(out)
        return cache["g"]

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (ValueError, KeyError) as exc:  # unknown/ambiguous table, bad reference: tell the agent how to fix it
            raise ToolError(str(exc.args[0] if isinstance(exc, KeyError) and exc.args else exc)) from None

    srv = MCPServer("keygraph", instructions=INSTRUCTIONS)

    @srv.tool()
    def search(query: str, limit: int = 10, with_paths: bool = True) -> dict:
        """Find tables by business meaning ("monthly revenue", "in transit", a status label). Use this first when you
        do not know which table holds something, then describe the top hit. Returns ranked tables with the reason each matched (names, business
        concepts, synonyms, metrics, notes, documented code labels), grain, primary key and pitfalls when known.
        Matches on profiled values never expose PII. with_paths=false leaves out the suggested join paths between the top hits."""
        g = graph()
        with ContextDB(out / "keygraph.db", g, readonly=True) as ctx:
            return run(S.search, g, ctx, query, limit, with_paths)

    @srv.tool()
    def describe(table: str, brief: bool = False) -> dict:
        """Everything known about one table: keys and indexes, columns, business context (grain, pitfalls, code
        meanings) and its relations grouped by role (parent, child, lookup, audit...) with confidence. Use it
        before querying a table you have not seen. Give schema.table when the bare name exists in several schemas.
        brief=true returns only keys, filter columns, table notes and columns as name/type/key/codes (no profiles or
        relations): use it for a first look at a wide table, then call again without it."""
        return run(describe_result, out, graph(), table, True, brief)

    @srv.tool()
    def keys(table: str) -> dict:
        """Primary key, unique keys and every index of a table, in column order. Use it before filtering a big
        table: filter on the leading column of an index so the query stays fast."""
        return run(Q.keys, graph(), table)

    @srv.tool()
    def explain(column_ref: str) -> dict:
        """What one column (schema.table.column) references and what references it, with the evidence for each
        relation. Inferred relations carry a confidence and evidence lines; declared foreign keys are 1.0."""
        return run(Q.explain, graph(), column_ref)

    @srv.tool()
    def path(from_table: str, to_table: str, max_hops: int = 4, min_confidence: float = 0.6, k: int = 3,
             include_mirrors: bool = False) -> dict:
        """Ranked join paths between two tables, each hop with its columns, confidence, evidence, fan-out warnings
        and a ready JOIN ... ON snippet. Use it before writing any join across more than one table. Inferred hops
        are guesses (check evidence, verify with a small SELECT when confidence is below 0.8 or a hop fans out).
        Paths avoid hub tables as stepping stones and mirror schemas (copies of another schema) unless
        include_mirrors is true."""
        return run(path_result, graph(), from_table, to_table, max_hops, min_confidence, k, include_mirrors)

    @srv.tool()
    def neighbors(table: str, depth: int = 1) -> dict:
        """Tables directly (depth 1) or transitively (depth 2 or 3; larger values are capped at 3) related to a table,
        with the relation kind and confidence. It has no confidence filter: weak inferred relations are included, so
        read each confidence. Use it to see what a table connects to when you do not have a target table in mind."""
        return run(Q.neighbors, graph(), table, max(1, min(int(depth), MAX_DEPTH)), 0.0)

    @srv.tool()
    def areas(include_mirrors: bool = False) -> dict:
        """The business areas: clusters of related tables (for example Ordering, Catalog, Payments), with size and
        most connected tables. Use it to get an overview of an unfamiliar database. Mirror schemas are left out
        unless include_mirrors is true. Returns {"areas": [...]}, the list the CLI prints with --json."""
        return {"areas": run(Q.list_areas, graph(), include_mirrors=include_mirrors)}

    @srv.tool()
    def area(name_or_table: str, include_mirrors: bool = False) -> dict:
        """One area in detail: its tables with grain, the relations inside it, neighbouring areas and hub labels.
        Give an area id, an area name (any case) or a table that belongs to it."""
        return run(area_result, out, graph(), name_or_table, include_mirrors, True)

    @srv.tool()
    def profile_summary(table: str) -> dict:
        """Stored value profiles for the columns of a table: null rate, distinct count, ranges, top values, recency and
        pattern hints, each with when and how it was sampled. Profiles are sampled and may be stale. PII columns never
        include raw values, only counts, length and pattern. This reads stored results only and never queries the
        database; columns is empty when the table was not profiled."""
        g = graph()
        t = run(g.resolve, table)
        with ContextDB(out / "keygraph.db", g, readonly=True) as ctx:
            prefix = t + "."
            cols = {k[len(prefix):]: v for k, v in ctx.load_profiles().items() if k.startswith(prefix)}
        return {"table": t, "columns": cols}

    db = out / "keygraph.db"

    @srv.tool()
    def code_where(table: str, column: str | None = None, kind: str | None = None, limit: int = 50) -> dict:
        """Where a table or one column appears in indexed source code: ORM declarations, SQL that writes or reads it,
        and uses of its properties, declarations first, each with repo, path, line, rule and confidence. kind filters
        to one of declare_table, declare_column, sql_write, sql_read, use. Returns empty refs when no repo was indexed."""
        ref = f"{table}.{column}" if column else table
        return run(CI.where, db, ref, kind, None, 0.0, max(1, min(int(limit), 500)))

    @srv.tool()
    def code_what(path: str | None = None, line: int | None = None, symbol: str | None = None, repo: str | None = None) -> dict:
        """The tables and columns a source file (or one line of it) or a symbol touches, from the code index. Give a
        path relative to the repo root (a trailing part such as models/order.py also works) and optionally a line, or
        a symbol such as getStatus. With a line, also returns the declarations of the entity the file declares."""
        return run(CI.what, db, path, line, symbol, repo)

    @srv.tool()
    def query_for(table: str, column: str | None = None) -> dict:
        """Logged queries that touched a table or column, each with its business question, normalized SQL, runs,
        median and p95 ms and the role of the column (filter, join, group, read, write). Use it to see how a table
        was queried before writing a new query."""
        return run(QL.for_ref, db, f"{table}.{column}" if column else table)

    @srv.tool()
    def query_top(by: str = "runs", table: str | None = None, limit: int = 10) -> dict:
        """The most used logged queries, by runs, recent use or number of people, optionally only those touching one
        table. Each comes with its business question, normalized SQL and timing (median, p95, recent trend)."""
        return run(QL.top, db, by, table, max(1, min(int(limit), 100)))

    @srv.tool()
    def query_search(text: str, limit: int = 10) -> dict:
        """Full-text search over the business questions and normalized SQL of logged queries ("late orders",
        "refund rate"). Returns matching queries with their tables and timing."""
        return run(QL.search, db, text, max(1, min(int(limit), 100)))

    @srv.tool()
    def query_submit(sql: str, source: str, ms: float | None = None, rows: int | None = None, context: str = "",
                     tags: list[str] | None = None, by: str = "") -> dict:
        """Log one query you ran so later questions can reuse it: the SQL, where it ran (source), how long it took
        (ms), the rows it returned and the business question it answered (context). Literal values are replaced by ?
        and secret-looking values are masked before storing. This is the only tool that writes, and it writes only the
        query log tables. Returns the query's fingerprint and the tables it touched."""
        return run(QL.add, db, sql, source, ms, rows, context, tags or [], by)

    def _read(rel: str) -> str:
        p = (out / rel).resolve()
        if out.resolve() not in p.parents or not p.is_file():
            raise ResourceError(f"no page {rel}")
        return p.read_text(encoding="utf-8")

    @srv.resource("keygraph://areas/index", mime_type="text/markdown")
    def areas_index() -> str:
        """Markdown index of all business areas."""
        return _read("areas/index.md")

    @srv.resource("keygraph://tables/{id}", mime_type="text/markdown")
    def table_page(id: str) -> str:
        """Markdown page for one table (schema.table): area, grain, keys and indexes, columns and relations."""
        try:
            t = graph().resolve(id)
        except ValueError as exc:
            raise ResourceError(str(exc)) from None
        return _read("tables/" + table_file(t))

    return srv
