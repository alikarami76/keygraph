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
2. **Scope.** "Which schemas or databases should it map?" Default: every schema that holds the application's tables; leave out copies and mirrors (the build does not warn about them: it marks a schema that mirrors another as `mirror_of` in `graph.json`, and path, areas and neighbors skip those tables unless asked; it only prints a warning when a mirror declaration cannot be applied). Also ask: "Are any schemas copies or replicas of another one (for example `ods_*`, `replica`, `reporting`)?"
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

Pick the first method that is available. Add the driver (optional: `--with <driver>`, or the pip package name) only when the table below names one; for SQLite or the demo, leave it out. Install the extras that match the answers: `live` (a live connection), `mcp` (always), `yaml` (context or docs files), `code` (code index or query log).

```bash
# uv (preferred: a persistent tool with a stable path that GUI agents can start)
uv tool install 'keygraph[live,mcp,yaml,code]' [--with <driver>]   # --with only when a driver is needed
# pipx
pipx install 'keygraph[live,mcp,yaml,code]'    # then, only if a driver is needed: pipx inject keygraph <driver>
# plain pip (inside a virtual environment)
python3 -m pip install 'keygraph[live,mcp,yaml,code]' [<driver>]
# an unreleased version from a clone (this may update the clone's uv.lock)
python3 -m pip install -e '/path/to/keygraph[live,mcp,yaml,code]'
# in zsh, set CLONE to the clone's path and quote the whole thing so the brackets are not a glob: pip install -e "${CLONE}[live,mcp,yaml,code]"
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

Check: `keygraph --help` prints the command list. If `keygraph` is not on PATH after a `pip install` inside a virtual environment, run `export PATH="<path to that venv>/bin:$PATH"` or call `<venv>/bin/keygraph` by its full path in every command below. If it is not on PATH after `uv tool install`, run `uv tool update-shell` and open a new shell, or call it by the absolute path `uv tool dir --bin` prints.

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

# (d) demo: copy examples/shop (keep a copy of the whole folder) and run the copy's build.sh. keygraph must be on PATH:
# for a virtual environment run `export PATH="<path to that venv>/bin:$PATH"` first (or call <venv>/bin/keygraph).
# build.sh changes into its own folder, so give it an absolute OUT. It imports the demo's context, code index, query
# log and profiles, and renders. After it run only the starter-context import (question 5, below) and a final
# `keygraph render`; skip the whole extras block that follows (`code index .` would index the wrong folder).
# Without an argument it writes next to the script.
OUT="$(pwd)/keygraph-out" && sh <path to your copy of examples/shop>/build.sh "$OUT"
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
# value profiles (an EXAMPLE for a real Postgres database; use your engine for --source). Bounded sampled reads, PII values never stored. Start with the most connected tables
( set -a; . ./.env; set +a; keygraph profile --source postgres --url "${DATABASE_URL}" --top-hubs 20 --with-context --dir "$OUT" )
# refresh the report, the explorer and the agent pages (add --queries if you imported a query log; the demo's build.sh renders with --queries)
keygraph render --dir "$OUT"
```

**Starter business context (question 5).** Do these in order. (For the demo, the starter file is the one you write here, for example `keygraph-context.yaml`; start from a real demo table such as `shop.orders`.)

1. Copy `setup/templates/context-starter.yaml` (or fetch it from the raw URL above) to `keygraph-context.yaml` in the project, and delete every example entry in it before you fill it in: its `schema.orders` placeholder can name a real table.
2. Fill it only with facts you can point to in the code or docs: what one row is, status code meanings from enums or constants, and metric formulas from existing reports. A short file is fine, and when every fact is already stored and verified you may add nothing.
3. Leave `verified: false`. Use `verified=true` only for a fact you checked against data or code in this session, never for a guess or something merely read in a comment. A fact the user told you that you cannot check stays unverified: add it with `source="user, <date>"` and no `verified`.
4. Before you import, look up each table, column, concept, metric and relation in your file with `keygraph describe <table> --brief --dir "$OUT"` (notes and codes already stored) or `keygraph search "<name>" --dir "$OUT"`.
5. Check the version by behaviour: keygraph 0.4.0+, or any build where `keygraph context history --json` works (`keygraph --version` is not supported). Only then does an import from another source never replace a verified entry (it keeps the verified one and prints a warning); an older keygraph does replace it. Read any such warning to the user, and leave out entries that duplicate a `verified: true` one unless the user wants yours instead (then add it with `verified=true`).
6. Import it as above, then tell the user that the file is theirs to correct, and that keygraph search gets better with every fact they confirm.

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

1. **Usage skill:** copy `skills/keygraph/SKILL.md` into your harness's skills folder (Cursor has no skills folder: save it as a `.cursor/rules/keygraph-guide.mdc` rule, see `harnesses/cursor.md`). It tells an agent when to use each tool and how to read confidence.
2. **Standing instructions:** add the block in `setup/templates/agent-instructions.md` to the instruction file of the project the map serves (if that is the keygraph repository itself, ask the user first) (`AGENTS.md`, `CLAUDE.md`, or a Cursor rule). Replace `{OUT}` with the output folder, and copy only the lines from `<!-- keygraph:start` to `<!-- keygraph:end -->` (the comment above the start marker is a note for maintainers, not part of the block). Keep the `<!-- keygraph:start -->` and `<!-- keygraph:end -->` markers so a later setup can replace the block in place. Small and local models follow standing instructions far better than a skill they have to choose, so never skip this.

## Step 7. Verify, end to end

1. `keygraph doctor --dir "$OUT"` must print `OK` or only `warn` lines (warnings are optional extras). Fix every `fail`.
2. Reload your harness's MCP servers as the harness file says (often a restart). If you cannot restart yourself, tell the user exactly what to click or run, and give them this check to paste into the new session: *"Use keygraph: search for <a business word>, then show the join path between <table A> and <table B>."*
3. Through the MCP tools (not the CLI), call `search` with a business word that fits this database, then `path` between two tables that should be related. Both must return results. Pick the words from what the build found, for example two tables joined by a declared foreign key (`keygraph describe <table> --dir "$OUT"` lists its relations; declared ones have confidence 1.00).

If you cannot call MCP tools until the harness restarts, run the same two calls in-process as a stand-in (below), and give the user the paste check from 7.2. Record in `OUT/SETUP.md` (a line "Verified: in-process stand-in, MCP not yet reloaded") which of the two you reached, so the next session knows:

```bash
# PYTHON is the interpreter keygraph is installed in: the "command" under mcp_launch in `keygraph doctor --json --dir "$OUT"`
PYTHON="<that command>"
"$PYTHON" -c 'import asyncio, sys; from keygraph.mcp_server import build_server; s = build_server(sys.argv[1]); print(asyncio.run(s.call_tool("search", {"query": "order"})).content[0].text)' "$OUT"
# the path call takes from_table and to_table
"$PYTHON" -c 'import asyncio, sys; from keygraph.mcp_server import build_server; s = build_server(sys.argv[1]); print(asyncio.run(s.call_tool("path", {"from_table": sys.argv[2], "to_table": sys.argv[3]})).content[0].text)' "$OUT" "<table A>" "<table B>"
```

The setup is done when step 7.3 works through MCP. If the harness cannot reload MCP servers without a restart, it is done when the in-process stand-in above returns results and the user has the paste check to run in the new session; say in your report which of the two you reached. (For a harness with no MCP, see `other.md`: the CLI with `--json` counts.)

## Step 8. Record the setup and report

1. Write `OUT/SETUP.md` from `setup/templates/setup-record.md`: the answers, the exact build commands (with `$KEYGRAPH_DB_URL`, never the value), the harness and scope, and the rebuild command.
2. If `git -C "$(dirname "$OUT")" rev-parse --is-inside-work-tree` fails, skip this step and tell the user the folder is not a git repository, so nothing was added to `.gitignore`. If it succeeds and `git check-ignore -q "$OUT"` fails, add `OUT` to `.gitignore` unless the user wants the map committed. `keygraph.db` and the profiles describe their data.
3. Tell the user, in five lines or fewer:
   - what was built, with the table and relation counts from `doctor`;
   - which extras are on;
   - where the map lives, and that it is connected to you;
   - how to rebuild after schema changes: one command, from the record;
   - one thing they can do to improve it: review `keygraph-context.yaml`, or confirm or reject the relations below 0.8 in `RELATIONS_REPORT.md`.

## Step 9. Optional: dev mode (default off)

Skip this step unless the user says they want to help improve keygraph or are developing it. Ask them once, in your report (step 8), not as a seventh question. Dev mode records how agents use keygraph, locally, and never records database rows or secrets (details: `docs/dev-mode.md`).

1. Turn it on, by one of two ways: run `keygraph dev on --dir "$OUT"`, or add `KEYGRAPH_DEV=1` to the MCP server's environment (the optional line in your harness file).
2. Verify: `keygraph dev status --dir "$OUT" --json` must show `"enabled": true`. If not, repeat 1 and read `source` in the output.
3. Restart the MCP server (the harness file says how), because the `dev_note` tool appears only if dev mode was on when the server started. Then check that `dev_note` is in the tool list. `keygraph doctor --dir "$OUT"` has a `dev_mode` line that says on or off.
4. Tell the user how to read it later: `keygraph dev report --dir "$OUT"`, and how to turn it off: `keygraph dev off --dir "$OUT"`.

## Keeping it current

- Rebuild after migrations: rerun the build command from `OUT/SETUP.md`, then re-import `keygraph-context.yaml` and any other context files (a rebuild that starts clean, like the demo's build.sh, drops notes saved with `context add`; put confirmed facts in the context file so they survive; `context add` merges (fields you do not give keep their stored values; `field=` with an empty value clears one), a file re-import adds or updates whole rows but does not remove links or entries you deleted from the file, so to remove one, rebuild from scratch and then import), then `keygraph render`. The MCP server picks up the new `graph.json` without a restart.
- When you confirm what a table, column or status code means while working, save it with (a fact the user told you that you cannot check: leave out `verified=true` and add `source="user, <date>"`) `keygraph context add --kind column --author "<your agent name>" table=<schema.table> column=<column> meaning="..." verified=true --dir "$OUT"` (`--kind` is required: table, column, concept, relation, metric or link; `context add` merges: fields you leave out keep their values, `codes=` merges code by code, `field=` empty clears one; `verified=true` only for what you checked against data or code), as the skill describes (or set `KEYGRAPH_AUTHOR` once for the session; without either the source label is `context-add`) (`keygraph context history` shows what a later write replaced or refused). If the output says `kept` or "unverified write ... ignored", a verified note already exists and yours was not stored; either leave it, or, if you checked it yourself, repeat the command with `verified=true`.
- After you run a query that answered a business question, log it with `query_submit`. The next agent can then reuse a proven query.
- If keygraph is wrong or breaks, follow `setup/BUG_REPORTS.md`.
