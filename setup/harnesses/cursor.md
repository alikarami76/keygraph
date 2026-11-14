# Cursor

`COMMAND` and `ARGS` below are `mcp_launch.command` and `mcp_launch.args` from `keygraph doctor --dir "$OUT" --json`.

## 1. Register the MCP server

Create or merge into `.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for all projects (ask first). Keep any other servers already in the file.

```json
{
  "mcpServers": {
    "keygraph": {
      "command": "/abs/path/to/python",
      "args": ["-m", "keygraph", "mcp", "--dir", "/abs/path/to/keygraph-out"]
    }
  }
}
```

Optional, dev mode (SETUP.md step 9): add `"env": { "KEYGRAPH_DEV": "1" }` next to `args`, or run `keygraph dev on --dir "$OUT"` and restart the server.

Cursor does not see your shell PATH, so `command` must be the absolute path. `doctor` already prints it.

## 2. Skill and standing instructions

- Rule: copy `setup/templates/cursor-rule.mdc` to `.cursor/rules/keygraph.mdc` and replace `{OUT}`.
- Cursor also reads `AGENTS.md`. If the project uses one, add the block from `setup/templates/agent-instructions.md` there instead of the rule. Use one or the other, not both.
- The full usage guide (`skills/keygraph/SKILL.md`) can be saved as `.cursor/rules/keygraph-guide.mdc` with `alwaysApply: false` and a description, so that Cursor attaches it when the agent works on SQL.

## 3. Verify

- Cursor Settings → MCP (or Tools & Integrations): `keygraph` shows a green dot and 20 tools (21 if dev mode is on). If it is red, click it to see the error. The usual cause is a non-absolute `command`.
- Open a new Agent chat and do step 7.3 of SETUP.md.
