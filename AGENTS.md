# AGENTS.md: using keygraph as an AI agent

> **Asked to set up or install keygraph for a user?** Follow [setup/SETUP.md](setup/SETUP.md). It detects their database and your harness, asks them up to six questions, builds the map, connects the MCP server, and verifies the result with `keygraph doctor`. To report a keygraph bug, follow [setup/BUG_REPORTS.md](setup/BUG_REPORTS.md).

keygraph maps how the tables in a database connect (keys, indexes, declared and inferred foreign keys, business context, sampled value profiles). Use it to find tables and join paths before you write SQL. It is read-only and deterministic. It never runs SQL for you and makes no LLM calls.

Commands below assume a built output directory (default `keygraph-out`, set with `--dir` or `KEYGRAPH_DIR`). If the keygraph MCP server is connected, call the tool with the same name instead of the CLI; it returns the same JSON as `--json` (except `areas`, which wraps the list as `{"areas": [...]}`). Table names resolve leniently: a bare name works when it is unique, or unique outside mirror and copy schemas (the original wins over its copies); otherwise write `schema.table`.

## Sequence

1. Locate the data. `keygraph search "<business term>" --json` ranks tables by meaning and says why each matched. If you do not know where to start, `keygraph areas --json` lists business areas and `keygraph area <name> --json` shows one area's tables, grain and relations. (The MCP `areas` tool returns the list wrapped as `{"areas": [...]}`.)
2. Understand each candidate. `keygraph describe <table> --json` gives the row grain, keys, columns, status codes, pitfalls and sampled profiles. For a first look at a wide table use `keygraph describe <table> --brief` (MCP: `describe` with `brief=true`): keys, filter columns, notes and columns as name, type, key and codes only. `keygraph search ... --no-paths` (MCP: `with_paths=false`) leaves out the suggested join paths. `describe` and `neighbors` hide relations below 0.6 confidence and report how many (`hidden_relations`, `hidden`); pass `--min-confidence 0` (MCP: `min_confidence=0`) to see the weak inferred ones, and verify those before trusting them.
3. Find the join path before writing any join. `keygraph path <A> <B> --json` returns ranked routes with columns, confidence, evidence, fan-out warnings and a `JOIN` snippet. Use the best route unless a hop is weak.
4. Choose index-friendly filters. `keygraph keys <table> --json` lists the primary key, unique keys and indexes in column order. Filter on the leading column of an index first, especially on large tables.
5. Check a single relation or column when needed. `keygraph explain <table.column> --json` shows what it references, what references it, and the evidence.
6. See how the data is used and queried. If code was indexed, `keygraph code where <table[.column]> --json` lists where it is declared, written, read and used, and `keygraph code what <path[:line]> --json` goes from code to tables. If queries were logged, `keygraph queries for <table[.column]> --json` and `keygraph queries top --table <table> --json` show earlier queries with their business question and timing; reuse a proven query shape instead of inventing one.
7. After you run a query, log it: `keygraph queries add --sql <file> --source <db> --ms <ms> --rows <n> --context "<the question>"`, or the MCP `query_submit` tool. Literals are masked before storing.

## LLM indexing

You can build the meaning layer yourself: undeclared relations, column and status-code meanings, table grain and pitfalls, concepts, metrics and performance notes. keygraph never calls a model. It gives you a packet of facts for a few tables, you answer with JSON, and it validates, probe-checks and stores the answer. Ask the user before you start: it spends your own tokens (`--budget`, default 200000). Needs a built map (`graph.json`).

Commands (all take `--dir DIR`, alias `--out`, and `--json`; pass `--json`). MCP tools of the same name (`index_start`, `index_next`, `index_submit`, `index_status`, `index_finish`) return the same JSON; there is no MCP tool for `replay` or `refresh`.

- `keygraph index start [--budget N] [--tasks relations,columns,tables,concepts,metrics,performance] [--url-env NAME | --cmd-env NAME] [--dialect D] [--max-units N] [--restart]` plans the work. A second `start` resumes the open job (a larger `--budget` raises it). On a finished job it refuses: use `refresh`, or `--restart` for a full new run (saved answers stay and count until their unit is submitted again).
- `keygraph index next --json` returns `{"status": "unit", "unit_id", "tasks", "instructions", "packet", "answer_schema", "budget_left", "attempt"}`, or `done` (run `finish`), `budget_exhausted` (with `unit_id`, `needed` and `resume`), or `finished`. It returns the same unit until you submit it.
- `keygraph index submit UNIT_ID FILE|- [--model NAME] [--probe-seconds 60] --json` sends the answer (MCP: `index_submit(unit_id, answer, model)` with `answer` as an object).
- `keygraph index status --json`: units pending, done, failed and skipped, budget `used` and `left`, `accept_rate`, `verify_rate`, `failed_units`.
- `keygraph index finish --json`: rebuilds `graph.json`, pages, report and explorer from all stored answers, writes `index/summary.json` and closes the job when nothing is pending.
- `keygraph index replay --json`: the same rebuild with no model, no database access and no summary file. `keygraph index refresh --json` (CLI only) re-plans after the map changed: it puts back failed units and units whose packet changed, adds new ones and drops removed ones.

The loop: `start`, then `next` and `submit` until `next` says `done`, then `finish`. Read `status` in every reply; the exit code is 0 for every protocol outcome (`rejected`, `budget_exhausted`) and 2 for an error (`keygraph: <message>` on stderr, `{"status": "error", "error": ...}` with `--json`).

Answer rules:

- One JSON object with a list for every requested kind (`relations`, `columns`, `tables`, `concepts`, `metrics`, `performance`); use `[]` when you have nothing. Write tables as `schema.table`, exactly as in the packet. Every item needs a short `reason` and a `confidence` from 0 to 1. Use only tables and columns the packet names. Never write personal values.
- A unit gets three attempts. A submit with a structure error, or with items rejected and none accepted, is `rejected`: nothing is stored, the unit stays pending, and the reply has `attempts_left`, `hint` and `next`. Fix the listed items (`rejected[].item` and `reason`; item `$` means the whole answer) and submit again for the same `unit_id`.
- `partially_accepted`: the accepted items are stored, the unit stays pending, and you resubmit only the rejected items (other kinds may be left out). The third rejected or partial submit returns `failed`: the unit keeps the deterministic map and earlier accepted items stay stored. An all-empty answer counts as `accepted`.
- A missing or unparseable answer file is an error and uses no attempt, for the first three per unit.
- Budget: every submit costs its answer size plus the packet (charged once per unit), about characters divided by 4. Submit never refuses, but `next` returns `budget_exhausted` when the next packet does not fit. Continue with `keygraph index start --budget N` for a larger N.
- Items that pass validation but fail a probe are counted in `accepted` and `probe_failed` and stay out of the graph.
- Do not repeat relations the packet already lists as declared or inferred (`candidate_relations`). A declared one is rejected; an inferred one is accepted but adds evidence only, and the reply counts it in `confirmed_existing`.
- If a rejection says a fact is already verified, drop that item: a verified note is never replaced (check `keygraph describe <table>` first). An answer with all lists empty is a valid resubmit.
- Reply shapes: `submit` returns `{status, accepted, rejected: [{item, reason}], unverified, verified, probe_failed, confirmed_existing, budget_left, probe_source, next}` plus `attempts_left`, `resubmit` and `hint` when the unit stays pending. `start` returns `{job_id, units, budget, used, pending, skipped, tasks, url_env, cmd_env, dialect, resumed, next}` (units past `--max-units` are `skipped`, not failed). `finish` and `replay` return `{units, kept, filled, probe_failed, facts, verified_facts, relations, llm_edges}`: `facts` counts stored facts by kind, `relations` counts proposed relations by status, `llm_edges` counts new edges (a repeat of an existing edge adds evidence only), `filled` counts empty fields of existing notes that got filled, `kept` counts facts not used because a stored note differs. `refresh` returns `{job_id, changed, retried, new, dropped, unchanged, pending, next}` (`retried` are failed units put back). `status` reports `accept_rate` (accepted out of all submitted items) and `verify_rate` (verified out of accepted; null before data, 0 with no probes).

Probes: to let keygraph check relations against data, the user exports an environment variable that holds a read-only connection (a database URL, or a command with `{sql}`), for example `KEYGRAPH_PROBE_URL`. Pass only its name: `--url-env KEYGRAPH_PROBE_URL` (or `--cmd-env NAME`). Never write a URL, password or command into a command line, a file or the chat; the MCP tools take names only. keygraph reads the variable when `submit` runs and only runs single SELECT statements (at most 40 per submit and 60 seconds, then the rest stay unverified). `probe_source` in the reply is `none`, `unset`, `url`, `cmd` or `error`; with `none`, `unset` or `error` the items are unverified.

Output: `graph.json` can hold edges with `source: "llm"`, `llm_boost` and `override_rejects`; `describe` gains `performance` and `provenance`. Files: `index/base_graph.json`, `index/ledger.json`, `index/answers/<unit_id>.json`, `index/summary.json`, and the job tables in `keygraph.db`. Commit the context files next to `index/answers/`: replay from a fresh checkout is deterministic, but code-join and business-context evidence live in `keygraph.db` and the context files. In dev mode each index call logs `index_start`, `index_next`, `index_submit`, `index_finish`, `index_replay` and `index_refresh` events with unit ids, counts, status words and probe check names only; never answers, rejection reasons, packets, instructions, probe values or database paths (details in [docs/dev-mode.md](docs/dev-mode.md)).

## Dev mode (optional)

If a `dev_note` tool is listed, call it once per user question, before your first keygraph call for it, with the question in the user's words and no personal data. If it is not listed, do nothing. Without MCP, run `keygraph dev note --dir <out> "<question>" --json`. Commands from separate shells are grouped by time in the report; for exact grouping, put `KEYGRAPH_DEV_SESSION=<session from the output>` in front of each later command, or `export` it if your shell keeps state; if it says dev mode is off, do nothing more. Details: [docs/dev-mode.md](docs/dev-mode.md).

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

When you confirm what a table, column or status code means, save it so the next search finds it: `keygraph context add --kind column table=schema.t column=status codes=1:new,2:paid meaning="..." verified=true`. Mark `verified=true` only if you checked it against data or code; an unverified note never replaces a verified one (you get a warning); only re-importing the same context file can update its own rows. `context add` merges into the stored row (fields you leave out keep their values; `codes=5:x` adds one code and keeps the rest; `codes=` clears them). Add `--author "<your name>"` (or set `KEYGRAPH_AUTHOR`) so the source label says who wrote it (default label `context-add`). Refused and replaced writes are kept: `keygraph context history --json` lists them. Record a wrong or right relation in an overrides file (`confirm`, `reject`, `add`, `aliases`) and rebuild.

## Do not

- Do not ask keygraph to run SQL. It has no tool that runs SQL (the only queries it sends are the SELECT probes of LLM indexing, through the environment variable the user named). Run your own read-only query with your own database access; `query_submit` only records a query you already ran.
- Do not write a multi-table join from column-name guesses when `path` can answer it.
- Do not present an inferred relation below 0.6 as established.
- Do not assume a profile represents every row.

## Files you can read directly

`areas/index.md` (every area), `areas/<area>.md` (tables, joins, a Mermaid ER diagram), `tables/<schema.table>.md` (grain, keys, columns, relations in both directions), `RELATIONS_REPORT.md` (all relations with evidence) and `graph.json` (everything, machine readable). `build.json` records when the map was built, by which keygraph version, from which source (kind and database name only) and which schemas it covers; an unknown-table error names those schemas, so retry with `--schema` or `--all-schemas` when the table lives elsewhere.

## Dev mode commands

`keygraph dev on|off|status|clear|note`, `dev report [--json] [--since D] [--session ID]` and `dev export FILE[.gz]` record and read how agents use keygraph. They are for the people running keygraph; use them only when asked.

## Build the graph

If no output directory exists: `keygraph scan --url <sqlalchemy-url> [--schema S] [--all-schemas] [--generic]` (needs `keygraph[live]`; MySQL, PostgreSQL, SQL Server and ClickHouse URLs keep comments, enums, row counts and partitions via their own adapter; `--generic` forces the plain inspector) or `keygraph load --columns columns.json ...` from `information_schema` dumps. Setup for MCP clients is in the README: https://github.com/alikarami76/keygraph#mcp
