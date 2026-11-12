# Unsloth Studio and other local-model setups

Unsloth Studio runs local models and can use MCP servers. It ships with some servers on by default and lets you add your own. keygraph helps local models more than anyone: a small model with the right join path beats a large model that guesses.

`COMMAND` and `ARGS` below are `mcp_launch.command` and `mcp_launch.args` from `keygraph doctor --dir "$OUT" --json`.

## 1. Register the MCP server

In Unsloth Studio's MCP settings, add a custom server:

| Field | Value |
|---|---|
| Name | `keygraph` |
| Type / transport | local command (`stdio`) |
| Command | `COMMAND` (absolute path) |
| Arguments | `ARGS` |

If you cannot change the settings yourself, give the user this table with the real values and wait for them to confirm.

## 2. Model and instructions

- **Pick a model that calls tools well.** Coder and instruct models of about 14B and up, or MoE models such as a 30B-A3B, work. Very small models (under 7B) often call the wrong tool or invent arguments.
- **Put the instructions in the system prompt.** Paste the block from `setup/templates/agent-instructions.md`, with `{OUT}` filled in, into the chat's system prompt or custom instructions. Local models rarely discover a skill on their own.
- **Fewer tools are better for small models.** If the UI lets you choose tools, start with `search`, `describe`, `path`, `keys` and `explain`. Add `code_*` and `query_*` later.

## 3. Verify

Start a new chat with the keygraph server on and do step 7.3 of SETUP.md. If the model answers without calling a tool, say so in the prompt ("use the keygraph tools") and check that the tools are switched on for that chat.

## Unsloth-trained models in another harness

If the user runs an Unsloth fine-tune or GGUF through OpenCode, Codex, Claude Code or another agent, follow that harness's file. Nothing here changes.
