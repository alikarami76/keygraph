# keygraph setup protocol (for AI agents)

You are an AI coding agent and your user asked you to set up keygraph. Follow these steps in order. Do not skip the verification step. Each step says what to run, what to check, and what to do if it fails.

Files in this protocol are on GitHub. If you only have the URL, read them from raw URLs:
`https://raw.githubusercontent.com/alikarami76/keygraph/main/<path>` (for example `.../main/setup/harnesses/codex.md`).

## What you are setting up

keygraph builds a map of the user's database: tables, keys, indexes, declared and inferred relations with confidence and evidence, business context, sampled value profiles, where tables are used in code, and past queries. You will connect that map to yourself through MCP, so that from now on you find tables and join paths with keygraph before writing SQL. keygraph is read-only. It never writes to the database, never runs the user's SQL, and makes no LLM calls.

## Ground rules (always)

1. **Read-only.** Never run SQL that writes or changes anything. keygraph's own commands are read-only. Use a read-only database user if the user has one.
2. **Secrets stay out of files and chat.** Never write a password, token or connection URL into an MCP config, a skill, AGENTS.md or any file you commit. Never print it. Pass it through an environment variable in the same shell command that runs keygraph. The MCP server needs no credentials: it only reads the built folder.
3. **Ask before global changes.** Writing to user-level config (all projects) needs the user's yes. Project-level files are fine once the user answered the questions in step 2.
4. **Idempotent.** If keygraph was set up before (a `SETUP.md` record exists in the output folder), update it in place. Do not add a second MCP server or a second instructions block.
5. **Stop on red.** If a step fails twice, stop, show the user the exact error, and follow `setup/BUG_REPORTS.md` if it looks like a keygraph bug.

## Step 1. Detect (no questions yet)

Find out as much as you can by yourself. Record what you find; you will show it to the user as defaults in step 2.

| What | How to find it |
|---|---|
| Your harness | You know which agent you are (Claude Code, Codex, Cursor, ZCode, OpenCode, Unsloth Studio, other). A Claude Code session with another model backend (DeepSeek, z.ai GLM, any Anthropic-compatible API) counts as Claude Code. |
| OS, Python | `python3 --version` (needs 3.11+). `uv --version` (preferred installer), else `pipx --version`. |
| Project folder | The current working directory. Is it a git repo? Is there a `.gitignore`? |
| Earlier setup | `keygraph-out/SETUP.md`, or `KEYGRAPH_DIR` in the environment. `keygraph doctor --json` if keygraph is already installed. |
| Database engine and how to reach it | Names (never values) of variables such as `DATABASE_URL`, `DB_HOST`, `POSTGRES_*`, `MYSQL_*` in `.env*`, `docker-compose*.yml`, `config/database.*`, `settings.py`, `application.yml`, `prisma/schema.prisma`. Look only at variable names and the engine (`postgres`, `mysql`, `sqlserver`, `sqlite`, `clickhouse`, `mongodb`). |
| Code that uses the database | ORM models, migrations, `.sql` files, query builders. If found, the code index is useful. |
| Existing documentation | `dbt_project.yml` and `models/**/*.yml`, `docs/` markdown about tables, a data dictionary. |
| A query log | Any JSONL of past queries, or a warehouse query history the user can export. |

## Step 2. Ask the user (one message, at most six questions)

Send all questions in one message. Put your detected answer next to each question as the default, so the user can reply "defaults" or change only what is wrong. Ask only what you could not detect. Use plain words.

1. **Source.** "How should keygraph read your database's structure?" Options:
   - (a) a live read-only connection through an environment variable (default when you found one: say its name, never its value);
   - (b) `information_schema` dump files;
   - (c) a read-only command that already works on this machine (for example a CLI that prints JSON rows);
   - (d) just try the bundled demo database first.
2. **Scope.** "Which schemas or databases should it map?" Default: every schema that holds the application's tables; leave out copies and mirrors (the build warns about them). Also ask: "Are any schemas copies or replicas of another one (for example `ods_*`, `replica`, `reporting`)?"
3. **Where.** "Keep the map in this project (`./keygraph-out`, git-ignored) and connect it to me for this project only?" Default yes. The other option is a shared folder (for example `~/.keygraph/<name>`) connected for all projects, which needs a user-level config change.
4. **Extras.** "Turn on these? Each makes my SQL more accurate." Give the defaults you chose:
   - value profiles: one bounded, sampled, read-only SELECT per table; personal-data values are never stored (default yes for a live connection);
   - code index of this repository (default yes when you found ORM models or SQL files);
   - import existing docs (dbt YAML, markdown) as business context (default yes when found);
   - import a query log (default yes when a file exists).
5. **Business context.** "Shall I draft starter notes (what one row means, status codes, key metrics) for the most-used tables from the code and docs, marked unverified, for you to review?" Default yes.
6. **Bug reports.** "If keygraph itself misbehaves, may I draft a GitHub issue for you to approve before anything is sent?" Default yes (draft, then ask). Other options: never.

Wait for the answers. Write them down; you will save them in step 8.

## Step 3. Install

Pick the first method that is available. Add the driver (`--with <driver>`, or the pip package name) only when the table below names one; for SQLite or the demo, leave it out. Install the extras that match the answers: `live` (a live connection), `mcp` (always), `yaml` (context or docs files), `code` (code index or query log).

```bash
# uv (preferred: a persistent tool with a stable path that GUI agents can start)
uv tool install 'keygraph[live,mcp,yaml,code]' --with <driver>
# pipx
pipx install 'keygraph[live,mcp,yaml,code]' && pipx inject keygraph <driver>
# plain pip (inside a virtual environment)
python3 -m pip install 'keygraph[live,mcp,yaml,code]' <driver>
# an unreleased version from a clone (this may update the clone's uv.lock)
python3 -m pip install -e '/path/to/keygraph[live,mcp,yaml,code]'
```

Database drivers for a live connection:

| Engine | Driver package | URL scheme |
|---|---|---|
| PostgreSQL | `psycopg[binary]` | `postgresql+psycopg://user@host:5432/db` |
| MySQL, MariaDB | `pymysql` | `mysql+pymysql://user@host:3306/db` |
| SQL Server | `pyodbc` (plus the ODBC driver) | `mssql+pyodbc://...` |
| SQLite | none | `sqlite:///path/to/file.db` |
| ClickHouse | `clickhouse-sqlalchemy` | `clickhouse+native://...` |
| MongoDB | `pymongo` | `mongodb://...` (profiles only, through `--source mongodb`) |

Check: `keygraph --help` prints the command list. If `keygraph` is not on PATH after `uv tool install`, run `uv tool update-shell` and open a new shell, or call it by the absolute path `uv tool dir --bin` prints.

## Step 4. Build the map

Use `OUT` for the output folder from question 3 (an absolute path). Put the connection URL in an environment variable named `KEYGRAPH_DB_URL` for the duration of the command only. If it lives in `.env`, load it in the same command without printing it:

```bash
# (a) live connection. Metadata only; add --schema once per schema, or --all-schemas
( set -a; . ./.env; set +a; KEYGRAPH_DB_URL="${DATABASE_URL}"; \
  keygraph scan --url "$KEYGRAPH_DB_URL" --schema public --dir "$OUT" )
# optional: add --copy-schema 'ods_*' for each schema that holds copies of another
# a plain postgresql:// URL makes SQLAlchemy look for psycopg2; with psycopg (v3) use
#   KEYGRAPH_DB_URL="${DATABASE_URL/postgresql:/postgresql+psycopg:}"

# (b) dumps exported with information_schema field names
keygraph load --columns columns.jsonl --statistics statistics.jsonl --key-columns key_columns.jsonl --dir "$OUT"

# (c) a read-only command template that prints JSON rows for {sql}
keygraph fetch --cmd 'mytool --sql {sql}' --out "$OUT/dumps"   # then load the dumps as in (b)

# (d) demo: clone the repo, then, with keygraph on PATH (for a venv: export PATH="$VENV/bin:$PATH");
# build.sh finds the demo files next to itself, so it works from any folder (or a copy of examples/shop);
# it already imports the demo's context, code index, query log and profiles, so skip the extras below except render.
# Without an argument it writes examples/shop/out next to the script.
OUT="$(pwd)/keygraph-out" && sh path/to/examples/shop/build.sh "$OUT"
```

Then, in this order, only the extras the user said yes to:

```bash
# business context the user already has (dbt YAML, a context file, markdown notes)
keygraph context import path/to/context.yaml --dir "$OUT"          # or a directory of YAML files
keygraph context import-notes docs/data --dir "$OUT"              # markdown notes with front matter
# code index of this repository (read as text, never executed)
keygraph code index . --name <repo-name> --dir "$OUT"
# query log (literals are masked before storing)
keygraph queries import queries.jsonl --dir "$OUT"
# value profiles: bounded sampled reads, PII values never stored. Start with the most connected tables
( set -a; . ./.env; set +a; keygraph profile --source postgres --url "${DATABASE_URL}" --top-hubs 20 --with-context --dir "$OUT" )
# (the demo's build.sh already profiles its tables; for another engine, change --source)
# refresh the report, the explorer and the agent pages
keygraph render --dir "$OUT"
```

**Starter business context (question 5).** Copy `setup/templates/context-starter.yaml` (or fetch it from the raw URL above) to `keygraph-context.yaml` in the project, and delete every example entry in it before you fill it in: its `schema.orders` placeholder can name a real table. Fill it only with facts you can point to in the code or docs: what one row is, status code meanings from enums or constants, and metric formulas from existing reports. Leave `verified: false`. Before you import it, run `keygraph context export --dir "$OUT"` and look up each table, column, concept, metric and relation in your file: since keygraph 0.3.4 an import from another source never replaces a verified entry (it keeps the verified one and prints a warning), but an older keygraph does replace it. Read any such warning to the user, and leave out entries that duplicate a `verified: true` one unless the user wants yours instead (then add it with `verified=true`). Then import it as above. Tell the user that the file is theirs to correct, and that keygraph search gets better with every fact they confirm.

**Copies and replicas.** If the scan prints a warning about ambiguous relations across schemas, or the user named copy schemas, rebuild with `--copy-schema GLOB` for each one.

Check: `keygraph doctor --dir "$OUT"` shows `ok    graph` and `ok    query` lines. If the build found 0 tables, the schema name or the database user's permissions are wrong: fix them before you go on.

## Step 5. Connect the MCP server to yourself

```bash
keygraph doctor --dir "$OUT" --json
```

Use `mcp_launch.command` and `mcp_launch.args` from that output exactly. Both are absolute paths, which GUI agents need. Then open your harness file and follow it:

| Harness | File |
|---|---|
| Claude Code (also with DeepSeek, z.ai GLM or any Anthropic-compatible backend) | `setup/harnesses/claude-code.md` |
| Codex (CLI, IDE extension, app) | `setup/harnesses/codex.md` |
| Cursor | `setup/harnesses/cursor.md` |
| ZCode (Z.ai) | `setup/harnesses/zcode.md` |
| OpenCode | `setup/harnesses/opencode.md` |
| Unsloth Studio and other local-model setups | `setup/harnesses/unsloth-studio.md` |
| Anything else: Claude Desktop, Zed, Windsurf, Cline, Goose, Continue, custom agents, agents with no MCP | `setup/harnesses/other.md` |

## Step 6. Teach yourself how to use keygraph

Each harness file says where these go. Do both:

1. **Usage skill:** copy `skills/keygraph/SKILL.md` into your harness's skills folder. It tells an agent when to use each tool and how to read confidence.
2. **Standing instructions:** add the block in `setup/templates/agent-instructions.md` to the instruction file of the project the map serves (if that is the keygraph repository itself, ask the user first) (`AGENTS.md`, `CLAUDE.md`, or a Cursor rule). Replace `{OUT}` with the output folder. Keep the `<!-- keygraph:start -->` and `<!-- keygraph:end -->` markers so a later setup can replace the block in place. Small and local models follow standing instructions far better than a skill they have to choose, so never skip this.

## Step 7. Verify, end to end

1. `keygraph doctor --dir "$OUT"` must print `OK` or only `warn` lines (warnings are optional extras). Fix every `fail`.
2. Reload your harness's MCP servers as the harness file says (often a restart). If you cannot restart yourself, tell the user exactly what to click or run, and give them this check to paste into the new session: *"Use keygraph: search for <a business word>, then show the join path between <table A> and <table B>."*
3. Through the MCP tools (not the CLI), call `search` with a business word that fits this database, then `path` between two tables that should be related. Both must return results. Pick the words from what the build found, for example two tables joined by a declared foreign key (`keygraph describe <table> --dir "$OUT"` lists its relations; declared ones have confidence 1.00).

If you cannot call MCP tools until the harness restarts, run the same two calls in-process as a stand-in (below), and give the user the paste check from 7.2:

```bash
# use the Python that keygraph is installed in (mcp_launch.command from doctor --json)
"$PYTHON" -c 'import asyncio, sys; from keygraph.mcp_server import build_server; s = build_server(sys.argv[1]); print(asyncio.run(s.call_tool("search", {"query": "order"})).content[0].text)' "$OUT"
```

The setup is done when step 7.3 works through MCP. If the harness cannot reload MCP servers without a restart, it is done when the in-process stand-in above returns results and the user has the paste check to run in the new session; say in your report which of the two you reached. (For a harness with no MCP, see `other.md`: the CLI with `--json` counts.)

## Step 8. Record the setup and report

1. Write `OUT/SETUP.md` from `setup/templates/setup-record.md`: the answers, the exact build commands (with `$KEYGRAPH_DB_URL`, never the value), the harness and scope, and the rebuild command.
2. If `git -C "$(dirname "$OUT")" rev-parse --is-inside-work-tree` succeeds and `git check-ignore -q "$OUT"` fails, add `OUT` to `.gitignore` unless the user wants the map committed. `keygraph.db` and the profiles describe their data.
3. Tell the user, in five lines or fewer:
   - what was built, with the table and relation counts from `doctor`;
   - which extras are on;
   - where the map lives, and that it is connected to you;
   - how to rebuild after schema changes: one command, from the record;
   - one thing they can do to improve it: review `keygraph-context.yaml`, or confirm or reject the relations below 0.8 in `RELATIONS_REPORT.md`.

## Keeping it current

- Rebuild after migrations: rerun the build command from `OUT/SETUP.md`, then re-import `keygraph-context.yaml` and any other context files (a rebuild that starts clean, like the demo's build.sh, drops notes saved with `context add`; put confirmed facts in the context file so they survive), then `keygraph render`. The MCP server picks up the new `graph.json` without a restart.
- When you confirm what a table, column or status code means while working, save it with `keygraph context add --author <agent> ... verified=true`, as the skill describes (`keygraph context history` shows what a later write replaced or refused).
- After you run a query that answered a business question, log it with `query_submit`. The next agent can then reuse a proven query.
- If keygraph is wrong or breaks, follow `setup/BUG_REPORTS.md`.
