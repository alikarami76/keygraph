# Changelog

## Unreleased

- `keygraph scan --url` now uses the dedicated adapter for MySQL/MariaDB, PostgreSQL, SQL Server and ClickHouse URLs (still SELECT-only), so scanned maps keep column comments, enum members, row-count estimates and partitions like `load` does. `--schema` and `--all-schemas` behave as before; SQLite and other databases keep the SQLAlchemy inspector, an adapter error falls back to it with a stderr warning, and the new `--generic` flag forces it. `build.json` records the path used.
- New `build.json` next to `graph.json` (build time, keygraph version, source kind and database or dump file names, covered schemas; never a URL, host, credentials or absolute path; `SOURCE_DATE_EPOCH` sets the time). An unknown-table error now says which schemas the map covers and when it was built, and suggests rebuilding with `--schema` or `--all-schemas`. `graph.json` and the pages stay byte-identical.
- An unverified context write (`context add`, or an import from another source) no longer replaces a verified note for the same concept, link, relation, column, metric or table; keygraph keeps the verified row and reports a warning. Only re-importing the same context file can update its own rows, and a verified write still replaces anything. Reading a YAML context file without PyYAML now fails with a clear message instead of a raw ImportError.
- Join paths now flag many-to-many hops (including partial composite-key hops) as fan-out and rank them lower, the same as one-to-many hops, in the CLI, MCP and explorer.

## 0.3.2 - 2026-11-04

- Inference prefers a related schema (`app` -> `ods_app`, `app_v2`) when a column's candidates are all in other schemas, and schemas can be declared copies by glob (`copies: ["ods_*"]` in the context or overrides file, `--copy-schema GLOB` on `scan`, `load` and `code index`): the code index prefers non-copy tables and inference prefers non-copy targets.
- SQL Server adapter: every metadata column is named in the query, so command tools that return rows keyed by column name (instead of tuples) read columns, keys and indexes instead of empty rows.
- Inference for databases without foreign keys. A table with no primary or unique key but an `id` column is treated as keyed on `id` as a link target (evidence "assumed key", scored 0.1 below a real primary key; no index is invented).
- Log tables: in `<base>_log`, `_logs`, `_history` and `_audit` tables, `entity_id`, `record_id` and `object_id` point at `<base>` (same schema, then the schema without a trailing `_log`, then a single unique match elsewhere).
- Relations between original tables are copied onto their copies in a mirror schema when the copy has none (confidence x 0.95, `source` "mirror"). Mirror tables stay out of areas as before.

## 0.3.1 - 2026-11-04

- Inference matches a column to a sibling table that shares this table's name prefix: `ad_campaign_banners.campaign_id` -> `ad_campaigns`, `promo_banner_stats.banner_id` -> `promo_banners`.
- Links seen in code: equality joins in SQL found by the code index (`code_joins`, needs sqlglot) and in logged queries become evidence at render time. An edge between the same columns rises to confidence 0.95; otherwise a new inferred edge with `source` "code" is added (`keygraph.code_links.merge_code_joins`).

## 0.3.0 - 2026-11-04

- Code index (`keygraph code index | where | what | stats`): links source code to the tables and columns of a built graph, read as text and never executed. Built-in rules for Doctrine, SQLAlchemy, Django, TypeORM, Prisma, JPA/Hibernate, GORM and Rails; SQL in string literals and `.sql` files, parsed with sqlglot (`keygraph[code]`) or matched by table name without it; uses of declared properties with a common-name guard; every link carries its rule and a confidence. Incremental re-index by file hash; names that match no table are reported as unresolved.
- User pattern files (`code-patterns.yaml`) for in-house framework conventions, with the same engine as the built-in rules.
- Query log (`keygraph queries add | import | top | slow | for | search | show | stats`): SQL that was run, its timing, who ran it and the business question, grouped by fingerprint (a regex normaliser; sqlglot is not used for fingerprints), linked to tables and columns (with roles filter, join, group, read and write when sqlglot is installed), with median, p95 and the recent trend. Literals are masked unless `--keep-literals`; secret-looking values are always masked.
- MCP tools `code_where`, `code_what`, `query_for`, `query_top`, `query_search` (read-only) and `query_submit`, the only tool that writes, and only to the query log.
- Explorer and table pages: "Used in code" per table and column, an "in code" count per table in the area panel, and with `--queries` the most-run queries per table. Code snippets appear only with `--snippets`.
- The shop demo gains a fictional app (`examples/shop/app`) and query log (`examples/shop/queries.jsonl`).

## 0.2.0 - 2026-11-03

- Question-first explorer views: Areas, Area, Path, Table, Column and Search, with a canvas limit of 40 boxes, 25 tables shown first in large areas, and groups of more than 8 relations collapsed.
- Hubs (top 1% by incoming relations, or 60% or more from role columns such as `created_by_id`) shown as labels, and business areas grouped deterministically, with curation through an `areas:` block in the context file.
- Mirror schemas detected and kept out of areas, hub counts and join paths (`--include-mirrors` restores them).
- Join path ranking with penalties for hub, fan-out (one-to-many) and log, archive or scratch tables, and fan-out warnings in the output.
- Agent documents written with each build: `areas/index.md`, `areas/<area>.md` with Mermaid ER diagrams, and `tables/<schema.table>.md`.
- New commands `areas` and `area`; `--json` on query commands; `--include-mirrors` on `path`, `areas` and `area`.
- Value profiles (`keygraph profile`): bounded sampling, null rate, ranges, top values, pattern hints, flags for undocumented or unseen status codes, and the PII rule that stores no raw values for personal data.
- Source adapters: MySQL/MariaDB, PostgreSQL, SQLite, SQL Server, ClickHouse, Metabase, MongoDB, information_schema dumps and external read-only commands, all limited to read-only access.
- MCP server (`keygraph mcp`) with nine read-only tools and two resources.
- Design system, logo and README visuals; fictional demo webshop in `examples/shop`; "How keygraph works" PDF.
- README, AGENTS.md, llms.txt, CITATION.cff and package metadata for discoverability.
- Python 3.11 works again (the CLI failed to import, and `context add --kind ... key=value` was rejected).
- Search leaves short words and stopwords out of prefix matching and ranks whole-phrase hits higher, so a status label such as "in transit" finds its column first.
- Malformed curation entries (area rules without a name or match, `hubs:` or `mirrors:` that are not mappings) become build warnings instead of crashes.
- `keygraph.html` carries a Content-Security-Policy; the MCP server closes its database handles on errors, reads older `keygraph.db` files, and caps `neighbors` depth at 3.
- Mermaid diagrams use safe entity names (no leading digit, ASCII only) and say when an area's diagram shows only its 25 most connected tables.

## 0.1.0

- Initial release: keys and indexes from the schema, declared and inferred foreign keys with confidence and evidence, join path search, business context in SQLite with full-text search, a self-contained HTML explorer, and the Claude Code skill.
