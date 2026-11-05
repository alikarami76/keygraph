# ZCode (Z.ai)

ZCode supports MCP servers from version 3.4.1. Its public docs are thin, so this file sticks to what the settings screen shows. If your version looks different, tell the user what you see and follow the same fields.

`COMMAND` and `ARGS` below are `mcp_launch.command` and `mcp_launch.args` from `keygraph doctor --dir "$OUT" --json`.

## 1. Register the MCP server

In ZCode: **Settings → MCP servers → Add**:

| Field | Value |
|---|---|
| Name | `keygraph` |
| Scope | user (or project, if offered and the user chose "this project") |
| Transport | `stdio` |
| Command | `COMMAND` (absolute path) |
| Arguments | `ARGS`, one per line or space-separated as the form asks |
| Timeout | `30000` ms |

If the screen offers a JSON editor instead, use the common `mcpServers` shape:

```json
{ "mcpServers": { "keygraph": { "command": "/abs/path/to/python", "args": ["-m", "keygraph", "mcp", "--dir", "/abs/path/to/keygraph-out"] } } }
```

You may not be able to edit ZCode's settings yourself. In that case, give the user the table above with the real values filled in, and wait for them to confirm.

## 2. Standing instructions

- Add the block from `setup/templates/agent-instructions.md` to `AGENTS.md` at the project root.
- If ZCode has a custom instructions or rules setting for the project, paste the same block there too. GLM models follow standing instructions well, and that is where keygraph's sequence (search, describe, path, keys) has to live.
- If your ZCode version supports skills or plugins with a `SKILL.md` folder, copy `skills/keygraph/SKILL.md` there as well.

## 3. Verify

- The MCP list in settings shows `keygraph` as connected with 15 tools.
- In a new task, do step 7.3 of SETUP.md.
