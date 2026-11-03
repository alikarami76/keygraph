# keygraph

keygraph maps a database's keys (primary keys, unique keys, every index with its column order, declared foreign keys)
and the relations between tables, including relations that are not declared as foreign keys. It also stores business
context next to the schema so people and agents can search by meaning. Everything is deterministic: no LLM calls.

## Install

    uv sync            # development
    pip install .      # or: pip install 'keygraph[live,yaml]'

Python 3.11 or newer. `live` adds SQLAlchemy for scanning a database; `yaml` adds YAML for overrides and context files.

## Build a graph

    keygraph scan --url postgresql://user@host/db --schema public            # live, read-only metadata
    keygraph load --columns columns.jsonl [--statistics stats.jsonl] [--key-columns kcu.jsonl]   # dumps

Dumps use `information_schema` field names. Use `--columns-map '{"TABLE_SCHEMA":"db"}'`, `--flatten raw` and
`--where field=value` to adapt other shapes. Without STATISTICS the tool falls back to COLUMN_KEY (PRI/UNI/MUL), and
without KEY_COLUMN_USAGE every relation is inferred. `keygraph fetch --cmd 'my-sql-cli --sql {sql}' --out dir` fetches
those two tables through any read-only command that prints JSON rows.

Output (default `keygraph-out/`): `graph.json`, `RELATIONS_REPORT.md`, `keygraph.html` (self-contained explorer that
works from disk, offline) and `keygraph.db` (SQLite with FTS5 for business context and search).

## Query

    keygraph path A B [--max-hops 4] [--min-confidence 0.6] [--json]   # ranked join paths with a JOIN snippet
    keygraph neighbors T [--depth 2]
    keygraph explain T.col                                              # references / referenced by, with evidence
    keygraph keys T                                                     # PK, unique keys, indexes in column order
    keygraph search "monthly revenue per seller"                        # tables by business meaning
    keygraph describe T                                                 # keys plus business context

Table names resolve leniently: a bare name works when it is unique across schemas, otherwise use `schema.table`.

## How relations are inferred

Declared foreign keys score 1.0. Otherwise a column such as `customer_id` or `customerId` is matched to a table named
`customer(s)` whose primary or unique key is a single column. Plurals, common prefixes, role prefixes (`buyer_user_id`
-> `users`), `*_by_id` columns (mapped to a users-like table), `parent_id` self-references and columns named like another
table's primary key are handled. `<x>_type` plus `<x>_id` pairs are marked polymorphic with no single target. Type
compatibility, an index on the column and a primary-key target raise confidence; type mismatch, cross-schema matches and
ambiguity lower it. Optional `keygraph probe` samples values (SELECT only) to measure the real match rate.

An overrides file (YAML or JSON) supports `aliases`, `role_aliases`, `prefixes`, `confirm`, `reject` and `add`:

    aliases: {seller: shop.sellers}
    confirm: [{from: shop.orders.customer_id, to: shop.customers.id}]
    reject:  [{from: shop.logs.ref_id}]

## Business context

Context lives in `keygraph.db`: concepts (with multilingual synonyms), concept links to tables and columns, concept
relations, column semantics (meaning, unit, status codes, null meaning, PII), metrics and table notes (grain,
freshness, soft-delete and date columns, pitfalls). Every row records source, verified flag and update time.

    keygraph context import context/            # YAML/JSON files
    keygraph context import-notes notes/        # one markdown note per table
    keygraph context add --kind column table=shop.orders column=status codes=1:new,2:paid meaning="order state"
    keygraph context export --out context.yaml

Unknown tables or columns produce warnings, not failures. Concept relations that match a graph edge add evidence to it.

## Explorer

`keygraph.html` shows an overview first and renders neighbourhoods on demand: search, click a table or a column, set
the depth (1 to 5) and the minimum confidence, filter by edge kind and schema, list everything reachable per hop
(CSV or JSON export) and find join paths with a copyable JOIN snippet. Library: cytoscape.js 3.30.2 (MIT), bundled.

## Tests

    uv run pytest
