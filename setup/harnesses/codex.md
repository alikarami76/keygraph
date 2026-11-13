# Codex (CLI, IDE extension, app)

`COMMAND` and `ARGS` below are `mcp_launch.command` and `mcp_launch.args` from `keygraph doctor --dir "$OUT" --json`. `args` is a JSON array: pass each element as its own word, for example `-- /abs/venv/bin/python -m keygraph mcp --dir /abs/keygraph-out`.

## 1. Register the MCP server

```bash
codex mcp add keygraph -- COMMAND ARGS...
```

Or edit `~/.codex/config.toml` yourself. Keep exactly one `[mcp_servers.keygraph]` block:

```toml
[mcp_servers.keygraph]
command = "/abs/path/to/python"
args = ["-m", "keygraph", "mcp", "--dir", "/abs/path/to/keygraph-out"]
```

Optional, dev mode (SETUP.md step 9): add `[mcp_servers.keygraph.env]` with `KEYGRAPH_DEV = "1"` under that block, or run `keygraph dev on --dir "$OUT"` and restart Codex.

This config is user-level (`~/.codex/config.toml`), shared by every project. Say so in step 2 of the setup, and when the user chose project-only, ask before you write it. To keep the map per project, point `--dir` at that project's output folder, and register a second server with a different name (for example `keygraph-billing`) for another database.

## 2. Skill and standing instructions

- Instructions: add the block from `setup/templates/agent-instructions.md` to `AGENTS.md` at the project root. Create the file if there is none. Codex reads AGENTS.md files from the repository root down to the working directory.
- Skill: copy `skills/keygraph/SKILL.md` to `.agents/skills/keygraph/SKILL.md` in the repository, or to `~/.agents/skills/keygraph/SKILL.md` for all projects.

## 3. Verify

- `codex mcp list` shows `keygraph`.
- Start a new Codex session (servers load at start) and run `/mcp`: keygraph should list 15 tools (16 if dev mode is on). Then do step 7.3 of SETUP.md.
