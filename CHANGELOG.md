# Changelog

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
