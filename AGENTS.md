# AGENTS.md: using keygraph as an AI agent

keygraph maps how the tables in a database connect (keys, indexes, declared and inferred foreign keys, business context, sampled value profiles). Use it to find tables and join paths before you write SQL. It is read-only and deterministic. It never runs SQL for you and makes no LLM calls.

Commands below assume a built output directory (default `keygraph-out`, set with `--dir` or `KEYGRAPH_DIR`). If the keygraph MCP server is connected, call the tool with the same name instead of the CLI; it returns the same JSON as `--json` (except `areas`, which wraps the list as `{"areas": [...]}`). Table names resolve leniently: a bare name works when it is unique across schemas, otherwise write `schema.table`.

## Sequence

1. Locate the data. `keygraph search "<business term>" --json` ranks tables by meaning and says why each matched. If you do not know where to start, `keygraph areas --json` lists business areas and `keygraph area <name> --json` shows one area's tables, grain and relations. (The MCP `areas` tool returns the list wrapped as `{"areas": [...]}`.)
2. Understand each candidate. `keygraph describe <table> --json` gives the row grain, keys, columns, status codes, pitfalls and sampled profiles.
3. Find the join path before writing any join. `keygraph path <A> <B> --json` returns ranked routes with columns, confidence, evidence, fan-out warnings and a `JOIN` snippet. Use the best route unless a hop is weak.
4. Choose index-friendly filters. `keygraph keys <table> --json` lists the primary key, unique keys and indexes in column order. Filter on the leading column of an index first, especially on large tables.
5. Check a single relation or column when needed. `keygraph explain <table.column> --json` shows what it references, what references it, and the evidence.
6. See how the data is used and queried. If code was indexed, `keygraph code where <table[.column]> --json` lists where it is declared, written, read and used, and `keygraph code what <path[:line]> --json` goes from code to tables. If queries were logged, `keygraph queries for <table[.column]> --json` and `keygraph queries top --table <table> --json` show earlier queries with their business question and timing; reuse a proven query shape instead of inventing one.
7. After you run a query, log it: `keygraph queries add --sql <file> --source <db> --ms <ms> --rows <n> --context "<the question>"`, or the MCP `query_submit` tool. Literals are masked before storing.

## How to read confidence

Declared foreign keys have confidence 1.0. Inferred relations are guesses from names, types, indexes and key shapes.

- 0.8 and above: use it.
- 0.6 to 0.8: read the evidence lines first.
- Below 0.6: never treat it as a fact. Verify it with a small `SELECT` (for example a count of rows whose value has no match) before relying on it. These edges are hidden by default; `path --min-confidence 0.4` shows them.
- A hop marked `fan-out` (one-to-many or many-to-many) turns one row into many. Aggregate before the join, or summing a column across it will count rows repeatedly.
- Polymorphic columns (`x_type` plus `x_id`) have no single target.
- Hub tables (such as a `users` table everything points to) appear as labels, and routes through hubs, log, archive or scratch tables are ranked lower on purpose.
- Tables in a mirror schema (a copy of another schema) are skipped unless you pass `--include-mirrors`.

## Profiles and context

Code refs carry a confidence too: declarations 1.0, SQL parsed by sqlglot 0.95 (0.7 when matched by table name only), uses 0.8 in files that name the entity and 0.5 elsewhere, divided when a name fits several tables. Read uses below 0.5 as hints.

Value profiles (top values, ranges, empty rates) come from a bounded sample and carry `profiled_at`, sample size and method. Treat them as samples that may be out of date, not as the full distribution. For personal data columns raw values are never stored; do not try to recover them.

When you confirm what a table, column or status code means, save it so the next search finds it: `keygraph context add --kind column table=schema.t column=status codes=1:new,2:paid meaning="..." verified=true`. Mark `verified=true` only if you checked it against data or code; an unverified note never replaces a verified one (you get a warning); only re-importing the same context file can update its own rows. Record a wrong or right relation in an overrides file (`confirm`, `reject`, `add`, `aliases`) and rebuild.

## Do not

- Do not ask keygraph to run SQL. It has no tool that runs SQL. Run your own read-only query with your own database access; `query_submit` only records a query you already ran.
- Do not write a multi-table join from column-name guesses when `path` can answer it.
- Do not present an inferred relation below 0.6 as established.
- Do not assume a profile represents every row.

## Files you can read directly

`areas/index.md` (every area), `areas/<area>.md` (tables, joins, a Mermaid ER diagram), `tables/<schema.table>.md` (grain, keys, columns, relations in both directions), `RELATIONS_REPORT.md` (all relations with evidence) and `graph.json` (everything, machine readable). `build.json` records when the map was built, by which keygraph version, from which source (kind and database name only) and which schemas it covers; an unknown-table error names those schemas, so retry with `--schema` or `--all-schemas` when the table lives elsewhere.

## Build the graph

If no output directory exists: `keygraph scan --url <sqlalchemy-url> [--schema S]` (needs `keygraph[live]`) or `keygraph load --columns columns.json ...` from `information_schema` dumps. Setup for MCP clients is in the README: https://github.com/alikarami76/keygraph#mcp
