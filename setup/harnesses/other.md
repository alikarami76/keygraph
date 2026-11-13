# Other agents and MCP clients

`COMMAND` and `ARGS` below are `mcp_launch.command` and `mcp_launch.args` from `keygraph doctor --dir "$OUT" --json`.

## Where the skill and the instructions go

There is no fixed place for a client that is not listed in `setup/harnesses/`. Put `skills/keygraph/SKILL.md` and the block from `setup/templates/agent-instructions.md` (only the lines between the `keygraph:start` and `keygraph:end` markers) where that agent loads project instructions or rules (its documentation says where), and write both paths into `OUT/SETUP.md`. If you cannot find such a place, tell the user and give them the block to paste.

## Clients that use the common `mcpServers` JSON

Claude Desktop (`claude_desktop_config.json`), Windsurf (`~/.codeium/windsurf/mcp_config.json`), Cline, Roo Code, Continue, Goose and most others take this shape. Merge it into the existing file:

```json
{ "mcpServers": { "keygraph": { "command": "/abs/path/to/python", "args": ["-m", "keygraph", "mcp", "--dir", "/abs/path/to/keygraph-out"] } } }
```

Optional, for dev mode (see SETUP.md step 9): add `"env": { "KEYGRAPH_DEV": "1" }` to the server entry, or run `keygraph dev on --dir OUT` and restart the server.

## Zed

In Zed's `settings.json`:

```json
{ "context_servers": { "keygraph": { "command": "/abs/path/to/python", "args": ["-m", "keygraph", "mcp", "--dir", "/abs/path/to/keygraph-out"] } } }
```

## Custom agents (Python, TypeScript, LangGraph, the OpenAI or Anthropic SDKs and others)

Connect to keygraph as a stdio MCP server with your framework's MCP client and the same command. Then put the block from `setup/templates/agent-instructions.md` in the system prompt.

## Agents with no MCP: use the CLI

Any agent that can run shell commands can use keygraph. Every query command takes `--json` and returns the same JSON as the MCP tool (one exception: the MCP `areas` tool wraps its list as `{"areas": [...]}`):

| MCP tool | CLI |
|---|---|
| `search` | `keygraph search "<words>" [--no-paths] --json --dir OUT` |
| `describe` | `keygraph describe <table> [--brief] [--min-confidence 0.6] --json --dir OUT` |
| `path` | `keygraph path <A> <B> --json --dir OUT` |
| `keys` | `keygraph keys <table> --json --dir OUT` |
| `explain` | `keygraph explain <table.column> --json --dir OUT` |
| `neighbors` | `keygraph neighbors <table> --depth 2 [--min-confidence 0.6] --json --dir OUT` |
| `areas` / `area` | `keygraph areas --json`, `keygraph area <name> --json` |
| `code_where` / `code_what` | `keygraph code where <t[.c]> --json`, `keygraph code what <path[:line]> --json` |
| `query_for` / `query_top` / `query_search` | `keygraph queries for <t>`, `queries top --table <t>`, `queries search "<q>"` (all with `--json`) |
| `query_submit` | `keygraph queries add --sql <file> --source <db> --ms <ms> --context "<question>"` |
| `dev_note` (dev mode only) | `keygraph dev note --json --dir OUT "<question>"` (put `--` before text starting with `-`), the report groups commands within 10 minutes into one session; for exact grouping put `KEYGRAPH_DEV_SESSION=<session>` in front of later commands |

No database yet? `keygraph demo --dir OUT --no-open --json` builds the bundled demo webshop (core install, no extras) and prints three commands to try.

Set `KEYGRAPH_DIR=OUT` in the agent's environment so you can leave out `--dir`. For step 7.3 of SETUP.md, the CLI version of `search` and `path` counts as the check.

Agents that can only read files can use the markdown pages: `OUT/areas/index.md`, `OUT/areas/<area>.md` and `OUT/tables/<schema.table>.md`.
