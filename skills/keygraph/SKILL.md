---
name: keygraph
description: Use for database join paths, missing or undeclared foreign keys, schema and ER diagram questions, schema context for text-to-SQL, finding which table holds a business concept, and what enum or status codes mean; also when an MCP database-schema server is wanted. keygraph maps keys, indexes and relations between tables (declared, and inferred with a confidence score and evidence), plus searchable business context and sampled value profiles. Use it before writing a multi-table SQL join or an index-friendly WHERE clause, and to build the graph from a live database or information_schema dumps. Also use it to find where a table or column is declared, written, read or used in source code (keygraph code where/what) and how a table was queried before (keygraph queries). Deterministic, no LLM calls; it never writes to the database.
---

# keygraph

keygraph is a deterministic CLI (no LLM calls). It builds `graph.json`, `RELATIONS_REPORT.md`, a self-contained `keygraph.html` explorer and `keygraph.db` (business context with full-text search) in one output directory (default `keygraph-out/`, override with `--dir` or `KEYGRAPH_DIR`). Run it with `uv run keygraph ...` or `keygraph ...`.

## When to use it

0. Orientation: `keygraph areas` lists the business areas (clusters of related tables) and `keygraph area <name>` shows one area's tables, grain, internal relations and neighbouring areas. Use it when you do not know where to start.
1. Finding data: `keygraph search "<business question or term>"` ranks tables by names, concepts, synonyms, metrics and notes, then shows grain, primary key, efficient filter columns, pitfalls and a join path between the top hits.
2. Before any join across more than one table: `keygraph path A B` gives ranked join paths with each edge's evidence and a ready `JOIN ... ON ...` snippet. Use `keygraph neighbors T --depth 2` to see what a table connects to.
3. Before filtering a big table: `keygraph keys T` lists the primary key, unique keys and indexes in column order. Filter on the leading column of an index. `keygraph describe T` adds the business context.
4. To understand one column: `keygraph explain T.col`.
All query commands take `--json`. Table names resolve leniently: a bare name works when unique across schemas, otherwise give `schema.table`.

5. Code and past queries: `keygraph code where T.col` lists ORM declarations, SQL writes and reads and property uses with file, line and confidence; `keygraph code what path:line` (or `--symbol getStatus`) goes from code to tables. `keygraph queries for T`, `queries top --table T`, `queries slow` and `queries search "<question>"` show earlier queries with their business question and timing. After running a query, log it with `keygraph queries add --sql f.sql --source <db> --ms <ms> --context "<question>"`. Index code with `keygraph code index REPO --name NAME [--patterns code-patterns.yaml]`.

MCP: if the keygraph MCP server is connected (`keygraph mcp --dir <out>`, see the README), call its tools (`search`, `describe`, `keys`, `explain`, `path`, `neighbors`, `areas`, `area`, `profile_summary`, `code_where`, `code_what`, `query_for`, `query_top`, `query_search`, `query_submit`) instead of the CLI; they return the same JSON as `--json`. All are read-only except `query_submit`, which only logs a query you ran.

## Read confidence honestly

Declared foreign keys have confidence 1.0. Inferred edges are guesses from names and key shapes: use 0.8 and above freely, check 0.6 to 0.8 against the evidence lines, and verify anything lower or marked `ambiguous` (and any `fan-out` hop, where one row becomes many) with a small SELECT before relying on it. Polymorphic columns (`x_type` plus `x_id`) have no single target. If a guess is right or wrong, record it in an overrides file (`confirm`, `reject`, `add`, `aliases`) and rebuild.

## Building the graph

- Live database: `keygraph scan --url <sqlalchemy-url> [--schema S] [--all-schemas]` (needs `keygraph[live]`; reads metadata only).
- Dumps: `keygraph load --columns columns.jsonl [--statistics s.jsonl] [--key-columns k.jsonl] [--columns-map '{"TABLE_SCHEMA":"db"}'] [--flatten raw] [--where field=value]`. Without statistics it falls back to COLUMN_KEY and marks indexes approximate.
- Fetch statistics and key usage with any read-only command: `keygraph fetch --cmd 'mytool --sql {sql}' --out dir`.
- Optional value-overlap check of inferred edges (SELECT only): `keygraph probe --url <url> --sample 500`.

## Column details and value insights

`describe`, `explain` and `keys` show full column types, defaults, enum members, comments and table engine, size and partitioning.
`keygraph profile --source <mysql|postgres|sqlite|sqlserver|clickhouse|mongodb> (--url U | --cmd '... {sql}') [--tables a,b] [--top-hubs N] [--with-context] [--sample 10000]`
samples values with one bounded read-only SELECT per table and stores null rate, distinct count, ranges, top values, recency and pattern hints, each with `profiled_at`, sample size and method.
Treat profiles as sampled and possibly stale. Codes seen in data but undocumented, or documented but never seen, are flagged: write them back with `context add`.
PII rule: for PII columns (by name, by value pattern such as email or phone, or a `pii` context flag) no raw value is ever stored, only counts, length and pattern.
Sources are pluggable (mysql, postgres, sqlite, sqlserver, clickhouse, metabase, mongodb, dumps, external commands); every one is read-only: single SELECTs only, Metabase GET plus native SELECT, Mongo find/list only.

## Business context (knowledge that accumulates)

When a run confirms what a table, column, status code or metric means, save it so the next search finds it:

    keygraph context add --kind table  table=schema.t grain="one row per ..." date_column=created_at pitfalls="..." verified=true source="run <id>"
    keygraph context add --kind column table=schema.t column=status codes=1:new,2:paid meaning="..." unit=... verified=true
    keygraph context add --kind concept name=order definition="..." synonyms=purchase,سفارش link=schema.orders::primary-record
    keygraph context add --kind relation from=customer rel=places to=order
    keygraph context add --kind metric name=revenue definition="..." expression="SUM(...)" tables=schema.a,schema.b

Bulk: `keygraph context import <file-or-dir.yaml>` and `keygraph context import-notes <dir-of-markdown>`. Mark `verified=true` only when it was checked against data or code. Unknown tables or columns only warn. `keygraph context export` dumps everything.

## Check and report

`keygraph doctor --dir <out>` checks the install, the map and the MCP server, and prints the exact MCP launch command with absolute paths. Run it after a setup or when a tool misbehaves. If keygraph itself is at fault (a crash, a missing declared key, a JOIN on columns that do not exist), follow https://github.com/alikarami76/keygraph/blob/main/setup/BUG_REPORTS.md: `keygraph doctor --report env.md` gives a redacted environment block. Never open an issue without the user's yes. Setting keygraph up for someone? Follow https://github.com/alikarami76/keygraph/blob/main/setup/SETUP.md.

## Explorer

`keygraph ui` opens `keygraph.html`: search, click a table or column, depth and confidence sliders, per-hop reachable list (CSV/JSON), path mode with a copyable JOIN snippet. It works from disk, offline.
