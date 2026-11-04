<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/readme-hero-dark.svg">
  <img alt="keygraph: map how your tables connect, for people and AI agents" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/readme-hero-light.svg">
</picture>

# keygraph

keygraph maps how the tables in your database connect: keys, indexes, declared and inferred foreign keys, business context and value profiles. People and AI agents use the map to find the right join path and the right data. It reads only metadata, makes no LLM calls, and gives the same output for the same input.

[![PyPI](https://img.shields.io/pypi/v/keygraph)](https://pypi.org/project/keygraph/) [![Python](https://img.shields.io/pypi/pyversions/keygraph)](https://pypi.org/project/keygraph/) [![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/alikarami76/keygraph/blob/main/LICENSE)

## When to use keygraph

Use keygraph when you need to:

- find the join path between two tables, with the join columns, a confidence for each hop and a ready `JOIN` snippet;
- infer missing foreign keys in a database that declares few or none;
- get an ER diagram of an existing database, grouped into business areas so it stays readable;
- give an LLM agent schema context for text-to-SQL: keys, indexes, join paths, grain, status codes and pitfalls, through MCP, a CLI with `--json`, or markdown files;
- find which table holds a business concept such as "net revenue" or "in transit";
- learn what an enum or status code means, and compare the documented codes with the values actually in the data;
- profile column values (top values, empty rate, ranges) with personal data masked;
- browse a data catalog from one offline HTML file, without running a server.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-quickstart-dark.svg">
  <img alt="Quickstart" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-quickstart-light.svg">
</picture>

## Quickstart

keygraph needs Python 3.11 or newer. Install it, build a graph from a live database, and open the explorer:

```bash
pip install 'keygraph[live]'     # or: uvx --from 'keygraph[live]' keygraph ...
keygraph scan --url postgresql://user@host/db --schema public       # read-only metadata scan, writes ./keygraph-out
keygraph ui                                                          # opens keygraph-out/keygraph.html
```

`scan` takes any SQLAlchemy URL, so add the driver for your database (for example `psycopg` for PostgreSQL or `pymysql` for MySQL). Then ask questions from the command line:

```bash
keygraph path customers shipments        # ranked join paths with a JOIN snippet
keygraph search "monthly revenue"        # which tables hold this
keygraph keys orders                     # primary key, unique keys, indexes in column order
```

To try it without a database, clone the repository and build the bundled fictional webshop (35 tables, three schemas):

```bash
git clone https://github.com/alikarami76/keygraph && cd keygraph
uv sync && uv run bash examples/shop/build.sh && uv run keygraph ui --dir examples/shop/out
```

The build writes `graph.json`, `RELATIONS_REPORT.md`, `keygraph.html` (a self-contained explorer that works from disk, offline), `keygraph.db` (SQLite with full-text search) and the agent pages `areas/*.md` and `tables/*.md`. Details of every step are in [How keygraph works (PDF)](https://github.com/alikarami76/keygraph/blob/main/docs/how-it-works.pdf) and [examples/shop](https://github.com/alikarami76/keygraph/blob/main/examples/shop/README.md).

If you cannot connect to the database, build from `information_schema` dumps instead:

```bash
keygraph load --columns columns.jsonl [--statistics stats.jsonl] [--key-columns kcu.jsonl] [--tables tables.jsonl]
```

Dumps use `information_schema` field names. `--columns-map '{"TABLE_SCHEMA":"db"}'`, `--flatten raw` and `--where field=value` adapt other shapes. Without STATISTICS the tool falls back to `COLUMN_KEY` (PRI/UNI/MUL), and without KEY_COLUMN_USAGE every relation is inferred. `keygraph fetch --cmd 'my-sql-cli --sql {sql}' --out dir` fetches those two tables through any read-only command that prints JSON rows.

## Supported sources

Each source is a module in `keygraph.adapters`, read-only by construction. SQL statements pass `assert_select` (a single SELECT, no write keywords, no `INTO OUTFILE` or `FOR UPDATE`); SQLite is read through the SQLAlchemy inspector, and MongoDB and Metabase are limited to the calls listed below.

| Source | What keygraph reads | How you use it |
|---|---|---|
| MySQL, MariaDB | `information_schema` (columns, indexes with column order, foreign keys, table facts, enum members) | `keygraph scan --url`, dumps, or the `mysql` adapter |
| PostgreSQL | `information_schema` and `pg_catalog` (indexes, foreign keys, enum types, row estimates, comments, checks) | `keygraph scan --url`, or the `postgres` adapter |
| SQLite | the SQLAlchemy inspector (metadata only) | `keygraph scan --url sqlite:///file.db` |
| SQL Server | `INFORMATION_SCHEMA` and `sys.*`; samples use `SELECT TOP n` | `sqlserver` adapter, `keygraph profile --source sqlserver` |
| ClickHouse | `system.tables`, `system.columns`, `system.data_skipping_indices`; the primary key becomes the PRIMARY index, `ORDER BY` an index | `clickhouse` adapter, `keygraph profile --source clickhouse` |
| Metabase | REST `GET /api/database/:id/metadata` (tables, fields, semantic types, foreign-key targets, field fingerprints) and native SELECTs through `POST /api/dataset`; no other request | `metabase` adapter (Python API) |
| MongoDB | schema inferred from sampled documents (nested paths become columns), indexes, `_id` as primary key, DBRef values as references; only `list_collection_names`, `find`, `index_information` and `estimated_document_count` can be called | `mongodb` adapter, `keygraph profile --source mongodb` |
| `information_schema` dumps (JSON, JSONL, CSV) | the files you give it | `keygraph load` |
| Any read-only command that prints JSON rows | the rows | `keygraph fetch --cmd` |

The Python entry point for the adapters is `keygraph.adapters.open_source(kind, ...)`, which returns an object with `schema()` and `sample()`.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-explorer-views-dark.svg">
  <img alt="Explorer views" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-explorer-views-light.svg">
</picture>

## Explorer views

`keygraph.html` is one file with no network requests. It is organised around the questions people ask: where does something live, how does it connect, what is in it. A canvas never draws more than 40 boxes: bigger areas are grouped and say what they hide, and an area over 25 tables first shows its 25 most connected tables.

**Areas.** The map of the whole database. Each box is a business area; the number on a line is how many relations join two areas. Start here when you do not know where something lives.

<img alt="keygraph Areas view: the database as business areas" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/docs/how-it-works/shots/areas.png" width="900">

**Area.** One area as a table diagram. Lines attach to the exact column that joins. Solid lines are declared foreign keys, dashed lines are inferred and carry their confidence. Hubs (tables that half the database points to) appear as a small label on each box instead of as dozens of lines.

<img alt="keygraph Area view: tables with key columns and relations" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/docs/how-it-works/shots/area.png" width="900">

**Path.** Pick two tables and get ranked routes, a copyable `JOIN`, and a warning at every step that can multiply rows (fan-out).

<img alt="keygraph Path view: join routes between two tables with fan-out warnings" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/docs/how-it-works/shots/path.png" width="900">

**Table.** One page per table: what a row is, pitfalls, the metrics that use it, and every column with its keys, value ranges and empty rate.

<img alt="keygraph Table view: grain, pitfalls, metrics and columns" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/docs/how-it-works/shots/table.png" width="900">

**Search.** Type a business word. Metrics, concepts, tables, columns and profiled values come from one index, so a status label such as "in transit" finds the column that stores it. Each result says why it matched.

<img alt="keygraph Search view: results grouped by type with the reason for each match" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/docs/how-it-works/shots/search.png" width="900">

There is also a Column view that sets documented status codes next to the values found in a sample. All screenshots come from the fictional demo in [examples/shop](https://github.com/alikarami76/keygraph/blob/main/examples/shop/README.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-ai-agents-dark.svg">
  <img alt="Use with AI agents" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-ai-agents-light.svg">
</picture>

## Use with AI agents

An agent can use the same graph four ways: MCP tools, the Claude Code skill, the CLI with `--json`, and plain markdown files. The recommended order for an agent is in [AGENTS.md](https://github.com/alikarami76/keygraph/blob/main/AGENTS.md): search or list the areas, describe the table, find the path before writing any join, and use `keys` to choose index-friendly filters.

Every query command (`search`, `describe`, `keys`, `explain`, `path`, `neighbors`, `areas`, `area`) takes `--json`. The build also writes `areas/index.md`, `areas/<area>.md` (tables, joins and a Mermaid ER diagram) and `tables/<schema.table>.md` (grain, keys, columns, relations in both directions) that an agent can read like any other file.

The Claude Code skill is in [skills/keygraph/SKILL.md](https://github.com/alikarami76/keygraph/blob/main/skills/keygraph/SKILL.md). It tells an agent when to use each command and how to read confidence.

### MCP

`keygraph mcp` serves the read-only queries to any MCP client over stdio. It needs the `mcp` extra and a built output
directory (`<out>` below, e.g. `keygraph-out`). Tools: `search`, `describe`, `keys`, `explain`, `path`, `neighbors`,
`areas`, `area` and `profile_summary` (stored, sampled profiles; PII values are withheld). Each returns the same JSON as
the CLI's `--json`, except that `areas` wraps the list of areas as `{"areas": [...]}` because MCP tool results must be objects. `path`, `areas` and `area` take `include_mirrors`. Resources: `keygraph://areas/index` and
`keygraph://tables/{schema.table}`. The server never runs SQL.

Use absolute paths for `--dir` and `--directory`. GUI clients (Claude Desktop, Cursor) often do not see your shell
`PATH`, so put the absolute path to `uvx` or `uv` (`which uvx`) in `command` if the server fails to start. The checkout
variant runs from a clone: `uv --directory /abs/path/to/keygraph run --extra mcp keygraph mcp --dir /abs/path/to/out`.

Claude Code:

    claude mcp add keygraph -- uvx --from 'keygraph[mcp]' keygraph mcp --dir /abs/path/to/out
    claude mcp add keygraph -- uv --directory /abs/path/to/keygraph run --extra mcp keygraph mcp --dir /abs/path/to/out

Claude Desktop (`claude_desktop_config.json`) and Cursor (`.cursor/mcp.json`):

    {
      "mcpServers": {
        "keygraph": {
          "command": "uvx",
          "args": ["--from", "keygraph[mcp]", "keygraph", "mcp", "--dir", "/abs/path/to/out"]
        }
      }
    }

    {
      "mcpServers": {
        "keygraph": {
          "command": "uv",
          "args": ["--directory", "/abs/path/to/keygraph", "run", "--extra", "mcp", "keygraph", "mcp", "--dir", "/abs/path/to/out"]
        }
      }
    }

Codex (`~/.codex/config.toml`):

    [mcp_servers.keygraph]
    command = "uvx"
    args = ["--from", "keygraph[mcp]", "keygraph", "mcp", "--dir", "/abs/path/to/out"]

    # or, from a checkout
    [mcp_servers.keygraph]
    command = "uv"
    args = ["--directory", "/abs/path/to/keygraph", "run", "--extra", "mcp", "keygraph", "mcp", "--dir", "/abs/path/to/out"]

(Use one `[mcp_servers.keygraph]` block, not both.)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-relations-inferred-dark.svg">
  <img alt="How relations are inferred" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-relations-inferred-light.svg">
</picture>

## How relations are inferred

Declared foreign keys score 1.0. For every other column, keygraph matches names and key shapes: a column such as `customer_id` or `customerId` is matched to a table named `customer(s)` whose primary or unique key is a single column. Plurals, common prefixes, role prefixes (`buyer_user_id` to `users`), `*_by_id` columns (mapped to a users-like table), `parent_id` self-references and columns named like another table's primary key are handled. `<x>_type` plus `<x>_id` pairs are marked polymorphic, with no single target.

Each inferred relation carries a confidence between 0 and 1 and the evidence lines that produced it. A compatible type, an index on the column, a primary-key target and a business relation in the context file raise it. A type mismatch, a cross-schema match and more than one possible target lower it. The optional `keygraph probe` samples values (SELECT only) to measure the real match rate. Read the number like this: 0.8 and above, use it; 0.6 to 0.8, read the evidence first; below 0.6, check it with a small SELECT. The explorer hides edges below 0.6 until you switch on "Show weak relations" (floor 0.4).

**Hubs.** A table in the top 1% by incoming relations, or one where at least 60% of incoming relations come from role columns such as `created_by_id`, is a hub. It is shown as a label, not as lines, so one `users` table does not turn every diagram into a hairball.

**Areas.** Tables that join each other (hubs removed) form a business area. The grouping is deterministic. A short `areas:` curation in the context file renames areas or pins tables to them.

**Path ranking.** `keygraph path` multiplies the confidence of each hop and applies penalties for routes through hubs, one-to-many (fan-out) steps and log, archive or scratch tables (names with a whole token such as `log`, `shadow` or `archive`), then prefers fewer hops. Each fan-out step is flagged in the output, because summing a column across it counts rows more than once.

**Mirrors.** A schema that holds copies of another schema's tables (a nightly reporting copy, say) is detected, marked as a mirror, and kept out of areas, hub counts and join paths so nothing is counted twice. `--include-mirrors` brings it back.

An overrides file (YAML or JSON) supports `aliases`, `role_aliases`, `prefixes`, `confirm`, `reject` and `add`:

```yaml
aliases: {seller: shop.sellers}
confirm: [{from: shop.orders.customer_id, to: shop.customers.id}]
reject:  [{from: shop.logs.ref_id}]
```

Pass it with `--overrides overrides.yaml` to `scan` or `load` and the next build uses it.

## Business context and value profiles

A schema knows column names, not that status 3 means "shipped". Context lives in `keygraph.db`: concepts (with multilingual synonyms), links from concepts to tables and columns, concept relations, column semantics (meaning, unit, status codes, null meaning, PII), metrics, and table notes (grain, freshness, soft-delete and date columns, pitfalls). Every row records its source, a verified flag and an update time.

```bash
keygraph context import context/            # YAML/JSON files
keygraph context import-notes notes/        # one markdown note per table
keygraph context add --kind column table=shop.orders column=status codes=1:new,2:paid meaning="order state"
keygraph context export --out context.yaml
```

Unknown tables or columns produce warnings, not failures. Concept relations that match a graph edge add evidence to it.

`keygraph profile --source mysql --url ... [--tables a,b] [--top-hubs 30] [--with-context] [--sample 10000]` samples each table with one bounded SELECT (never a full scan) and stores, per column: null rate, distinct values in the sample, min and max, percentiles, length statistics, date range and recency, top values with counts, and pattern hints (json, email, phone, url, uuid, Persian text, monetary). Each profile records `profiled_at`, the sample size and the method, so readers know how fresh and approximate it is. Codes seen in data but undocumented, and documented codes never seen, are flagged. Treat profiles as samples.

**PII rule.** A column is treated as personal data when its name matches (email, mobile, phone, national id, first, last or full name, address, postal code, token, password, iban, card number, birth date, ip address, api key and similar), when sampled values look like email addresses or phone numbers, or when a context entry sets `pii`. For those columns raw values are never stored: no top values, minimum, maximum or percentiles, only counts, null rate, length statistics and the pattern name. The rule is enforced in `profile_values` and covered by tests.

## Comparison

These tools solve neighbouring problems. The table says what each is for and where keygraph overlaps or stops.

| Tool | What it is for | How keygraph differs |
|---|---|---|
| SchemaSpy | Generates static HTML documentation and ER diagrams from a live database. | SchemaSpy also guesses implied relationships from column names (`-noimplied` turns this off). keygraph adds a confidence score and evidence for each guess, ranked join paths with fan-out warnings, business context, value profiles, MCP and agent files. SchemaSpy is the more established documentation generator. |
| dbdocs (DBML) | Publishes database documentation from DBML files that describe a schema. | keygraph reads an existing database or its dumps instead of a hand-written schema file. It does not host documentation online. |
| DataHub, OpenMetadata | Metadata platforms run as servers: ingestion, lineage, ownership, governance, glossary. | keygraph is a command-line tool that writes a folder of files. It has no server, users, lineage or governance. It is smaller in scope and needs no deployment. |
| Database IDE diagrams (DBeaver, DataGrip) | Generate ER diagrams from an existing database, mostly from its declared foreign keys. | keygraph groups a large schema into areas so each diagram stays readable, and shows inferred relations next to declared ones. |
| Diagram design tools (dbdiagram) | Design a schema as code and draw it, usually before the database exists. | keygraph starts from an existing database and does not design or change schemas. |
| Text-to-SQL schema linking | Choose which tables and columns are relevant to a natural-language question, often with a model. | keygraph supplies the inputs such a system can use (keys, join paths with fan-out warnings, grain, status codes). It does not turn a question into SQL and it makes no LLM calls. |

What keygraph does not do: it does not run your queries, generate SQL beyond a `JOIN` snippet, track lineage, monitor data quality, manage access, or edit a database.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-faq-dark.svg">
  <img alt="FAQ" src="https://raw.githubusercontent.com/alikarami76/keygraph/main/design/visuals/section-faq-light.svg">
</picture>

## FAQ

### How do I find the join path between two tables?
Run `keygraph path A B`. It returns ranked routes with the join columns, each hop's confidence and evidence, fan-out warnings and a ready `JOIN ... ON ...` snippet. Use `--max-hops` and `--min-confidence` (default 0.6) to widen or narrow the search, and `--json` for agents. The Path view in the explorer shows the same routes.

### How do I infer missing foreign keys?
Build the graph with `keygraph scan` or `keygraph load`. Every column that looks like a reference gets an inferred relation with a confidence and the reasons. Names such as `customer_id` are matched to a table with a single-column key, and an index or a primary-key target raises the score. `keygraph probe` can then sample values to check the match rate.

### How do I get an ER diagram from an existing database?
Build the graph and open `keygraph.html`, or read `areas/<area>.md`, which contains a Mermaid ER diagram for each business area. Large databases are split into areas. The Mermaid diagram draws at most an area's 25 most connected tables and says so on the page when it leaves tables out; the explorer's canvas never draws more than 40 boxes. In the explorer, declared foreign keys are solid lines and inferred ones are dashed.

### How do I give an LLM schema context for text-to-SQL?
Connect the keygraph MCP server, or let the agent call the CLI with `--json`. The agent should search, describe the tables, ask for the path before writing a join, and use `keys` for index-friendly filters. Grain, status codes, pitfalls and fan-out warnings come with the answers, which are the things a model otherwise has to guess. See [AGENTS.md](https://github.com/alikarami76/keygraph/blob/main/AGENTS.md).

### How do I find which table holds some data?
Run `keygraph search "net revenue"`. Search ranks tables by names, synonyms, concepts, metrics, notes, column codes and profiled values, and each hit says why it matched, what one row is, and which columns to filter on. Business meaning comes from the context file you provide, so the better it is, the better the answers.

### How do I find out what an enum or status code means?
Run `keygraph describe table` or `keygraph explain table.column`. Documented codes come from your context file, enum members come from the schema, and after `keygraph profile` the sampled values are shown beside them. Codes present in data but not documented are flagged.

### Is keygraph read-only and safe on production?
Yes, it never writes to your database. A scan reads metadata only. `profile` and `probe` run single, bounded SELECT statements, and every statement passes a check that rejects anything that is not one SELECT. Even so, point it at a replica if your policy requires it.

### Does keygraph send data anywhere?
No. It makes no network requests of its own beyond the connection to the database you choose, and it calls no LLM. The explorer is one HTML file that works offline, with its libraries bundled.

### Does it work on large databases?
It is built for them. The overview shows areas, not tables. An area over 25 tables first shows its 25 most connected tables (in the explorer and in the Mermaid diagram of `areas/<area>.md`), and no explorer canvas draws more than 40 boxes. Profiling samples with one bounded SELECT per table and can skip tables above a row limit (`--skip-over-rows`). No benchmarks are published here.

### How do I set up the MCP server?
Install `keygraph[mcp]`, build an output directory, then register `keygraph mcp --dir /abs/path/to/out` with your client. Copy-paste configs for Claude Code, Claude Desktop, Cursor and Codex are in the [MCP section](#mcp) above. The server exposes nine read-only tools and never runs SQL.

### How does confidence work?
Confidence is a score from 0 to 1 for an inferred relation. Declared foreign keys are 1.0; inferred ones are raised or lowered by name match, types, indexes, key targets, ambiguity and cross-schema matches, and every reason is listed as evidence. Use 0.8 and above freely, read the evidence between 0.6 and 0.8, and verify anything lower.

### How do I correct a wrong relation?
Write it in an overrides file with `confirm`, `reject`, `add` or `aliases`, and rebuild with `--overrides`. To rename areas or move tables between them, use the `areas:` curation in the context file. Business meaning goes back through `keygraph context add`.

## More

- [How keygraph works](https://github.com/alikarami76/keygraph/blob/main/docs/how-it-works.pdf) (PDF with screenshots)
- [examples/shop](https://github.com/alikarami76/keygraph/blob/main/examples/shop/README.md), a fictional webshop you can build in a minute
- [AGENTS.md](https://github.com/alikarami76/keygraph/blob/main/AGENTS.md), [skills/keygraph/SKILL.md](https://github.com/alikarami76/keygraph/blob/main/skills/keygraph/SKILL.md), [llms.txt](https://github.com/alikarami76/keygraph/blob/main/llms.txt)
- [Design system](https://github.com/alikarami76/keygraph/blob/main/design/DESIGN.md) and [CHANGELOG](https://github.com/alikarami76/keygraph/blob/main/CHANGELOG.md)

## Read-only guarantees

- `scan` reads metadata only. It never reads table rows.
- `profile` and `probe` send single SELECT statements, one bounded sample per table. Anything else is rejected before it reaches the database.
- Metabase: GET metadata plus native SELECT. MongoDB: list, find and index reads only.
- Personal data is never stored in profiles (the PII rule above).
- The explorer is one self-contained HTML file with no runtime network fetches.

## Development

```bash
uv sync
uv run pytest
```

## License

MIT. See [LICENSE](https://github.com/alikarami76/keygraph/blob/main/LICENSE). If you use keygraph in research, see [CITATION.cff](https://github.com/alikarami76/keygraph/blob/main/CITATION.cff).
