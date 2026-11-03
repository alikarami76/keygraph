---
name: keygraph
description: Map a database's keys (primary, unique, every index with column order, declared foreign keys) and the relations between tables, including undeclared ones inferred from naming with a confidence score, plus searchable business context. Use before writing a multi-table SQL join, when you need the right join path between tables, an index-friendly WHERE clause, or when you must find which tables hold a business concept; also to build or update the graph from a live database or information_schema dumps.
---

# keygraph

keygraph is a deterministic CLI (no LLM calls). It builds `graph.json`, `RELATIONS_REPORT.md`, a self-contained `keygraph.html` explorer and `keygraph.db` (business context with full-text search) in one output directory (default `keygraph-out/`, override with `--dir` or `KEYGRAPH_DIR`). Run it with `uv run keygraph ...` or `keygraph ...`.

## When to use it

1. Finding data: `keygraph search "<business question or term>"` ranks tables by names, concepts, synonyms, metrics and notes, then shows grain, primary key, efficient filter columns, pitfalls and a join path between the top hits.
2. Before any join across more than one table: `keygraph path A B` gives ranked join paths with each edge's evidence and a ready `JOIN ... ON ...` snippet. Use `keygraph neighbors T --depth 2` to see what a table connects to.
3. Before filtering a big table: `keygraph keys T` lists the primary key, unique keys and indexes in column order. Filter on the leading column of an index. `keygraph describe T` adds the business context.
4. To understand one column: `keygraph explain T.col`.
All query commands take `--json`. Table names resolve leniently: a bare name works when unique across schemas, otherwise give `schema.table`.

## Read confidence honestly

Declared foreign keys have confidence 1.0. Inferred edges are guesses from names and key shapes: use 0.8 and above freely, check 0.6 to 0.8 against the evidence lines, and verify anything lower or marked `ambiguous` (and any `fan-out` hop, where one row becomes many) with a small SELECT before relying on it. Polymorphic columns (`x_type` plus `x_id`) have no single target. If a guess is right or wrong, record it in an overrides file (`confirm`, `reject`, `add`, `aliases`) and rebuild.

## Building the graph

- Live database: `keygraph scan --url <sqlalchemy-url> [--schema S] [--all-schemas]` (needs `keygraph[live]`; reads metadata only).
- Dumps: `keygraph load --columns columns.jsonl [--statistics s.jsonl] [--key-columns k.jsonl] [--columns-map '{"TABLE_SCHEMA":"db"}'] [--flatten raw] [--where field=value]`. Without statistics it falls back to COLUMN_KEY and marks indexes approximate.
- Fetch statistics and key usage with any read-only command: `keygraph fetch --cmd 'mytool --sql {sql}' --out dir`.
- Optional value-overlap check of inferred edges (SELECT only): `keygraph probe --url <url> --sample 500`.

## Business context (knowledge that accumulates)

When a run confirms what a table, column, status code or metric means, save it so the next search finds it:

    keygraph context add --kind table  table=schema.t grain="one row per ..." date_column=created_at pitfalls="..." verified=true source="run <id>"
    keygraph context add --kind column table=schema.t column=status codes=1:new,2:paid meaning="..." unit=... verified=true
    keygraph context add --kind concept name=order definition="..." synonyms=purchase,سفارش link=schema.orders::primary-record
    keygraph context add --kind relation from=customer rel=places to=order
    keygraph context add --kind metric name=revenue definition="..." expression="SUM(...)" tables=schema.a,schema.b

Bulk: `keygraph context import <file-or-dir.yaml>` and `keygraph context import-notes <dir-of-markdown>`. Mark `verified=true` only when it was checked against data or code. Unknown tables or columns only warn. `keygraph context export` dumps everything.

## Explorer

`keygraph ui` opens `keygraph.html`: search, click a table or column, depth and confidence sliders, per-hop reachable list (CSV/JSON), path mode with a copyable JOIN snippet. It works from disk, offline.
