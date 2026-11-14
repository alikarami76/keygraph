# OpenCode

In the `command` array below, put `mcp_launch.command` first, then every item of `mcp_launch.args`, from `keygraph doctor --dir "$OUT" --json`.

## 1. Register the MCP server

Merge this into `opencode.json` at the project root, or into `~/.config/opencode/opencode.json` for all projects (ask first). Keep any other keys already there. OpenCode takes the command and its arguments as **one array**:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "keygraph": {
      "type": "local",
      "command": ["/abs/path/to/python", "-m", "keygraph", "mcp", "--dir", "/abs/path/to/keygraph-out"],
      "enabled": true,
      "timeout": 20000
    }
  }
}
```

Optional, dev mode (SETUP.md step 9): pass `KEYGRAPH_DEV=1` in the server's environment only if you have confirmed from your OpenCode version's docs how it passes environment variables to MCP servers (otherwise skip), or run `keygraph dev on --dir "$OUT"` and restart OpenCode.

## 2. Skill and standing instructions

- Instructions: add the block from `setup/templates/agent-instructions.md` to `AGENTS.md` at the project root. OpenCode reads it.
- Skill: if your OpenCode version loads skills (check OpenCode's documentation for the skills folder; if you are unsure, skip this, the AGENTS.md block is enough), copy `skills/keygraph/SKILL.md` there. The AGENTS.md block is enough without it.
- Local models through OpenCode (Ollama, llama.cpp, LM Studio, Unsloth GGUF builds): pick a model that is good at tool calling. If OpenCode lets you allow only some tools per agent, allow at least `keygraph_search`, `keygraph_describe`, `keygraph_path`, `keygraph_keys` and `keygraph_explain`. OpenCode adds the server name in front of the server's own tool names (`search`, `describe`, `path`, `keys`, `explain`).

## 3. Verify

- `opencode mcp list` (if your version has it), or start OpenCode and check that the keygraph tools are listed. If the `opencode` binary is not installed here, skip this check, use the in-process stand-in in SETUP.md step 7.3, and tell the user to paste the check from step 7.2 into a new OpenCode session.
- Do step 7.3 of SETUP.md in a new session.
