# Claude Code

This also covers Claude Code running on another model backend (DeepSeek, z.ai GLM, a local Anthropic-compatible server) through `--settings` or `ANTHROPIC_BASE_URL`. MCP servers, skills and CLAUDE.md work the same way whatever model is behind them.

`COMMAND` and `ARGS` below are `mcp_launch.command` and `mcp_launch.args` from `keygraph doctor --dir "$OUT" --json`.

## 1. Register the MCP server

```bash
# this project only, private to the user (default answer to question 3)
claude mcp add keygraph --scope local -- COMMAND ARGS...
# every project (ask the user first)
claude mcp add keygraph --scope user -- COMMAND ARGS...
```

Example: `claude mcp add keygraph --scope local -- /Users/me/.local/share/uv/tools/keygraph/bin/python -m keygraph mcp --dir /Users/me/app/keygraph-out`

Do not use `--scope project` (which writes `.mcp.json`, usually committed) unless the user wants teammates to share it. The absolute paths are personal, so for a team commit a version that uses `uvx --from 'keygraph[mcp]' keygraph mcp --dir ./keygraph-out` instead.

If `keygraph` is already registered, remove the old entry first (`claude mcp remove keygraph --scope <scope>`).

## 2. Skill and standing instructions

- Skill: copy `skills/keygraph/SKILL.md` to `.claude/skills/keygraph/SKILL.md` (this project) or `~/.claude/skills/keygraph/SKILL.md` (all projects).
- Instructions: add the block from `setup/templates/agent-instructions.md` to the project's `CLAUDE.md`. Create the file if there is none. If the project has an `AGENTS.md` and its `CLAUDE.md` contains `@AGENTS.md`, put the block in `AGENTS.md` instead.

## 3. Verify

- `claude mcp list` shows `keygraph: ... ✓ Connected`.
- MCP servers load when a session starts. Ask the user to start a new session (or run `/mcp` and reconnect), then do step 7.3 of SETUP.md there. The tools appear as `mcp__keygraph__search`, `mcp__keygraph__path` and so on.

## Notes for non-Anthropic backends

- Weaker or smaller models pick skills less reliably. The CLAUDE.md block is what keeps them on track, so keep it.
- If tool calls time out on a slow backend, keygraph is not the cause: its tools answer from local files in milliseconds.
