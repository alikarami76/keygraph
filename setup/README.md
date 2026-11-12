# Set up keygraph with your AI agent

Paste this into your coding agent (Claude Code, Codex, Cursor, ZCode, OpenCode, Unsloth Studio or another one):

> Set up keygraph for this project. Follow https://github.com/alikarami76/keygraph/blob/main/setup/SETUP.md exactly.

Your agent then:

1. **looks around first**: your database engine, where the connection settings are (names only, never the secrets), ORM models, dbt docs, query logs;
2. **asks you up to six questions** in one message, with its own guess as the default for each: which database and schemas, where to keep the map, which extras to turn on, whether to draft business notes, and whether it may draft bug reports for your approval;
3. **installs keygraph** and builds the map: read-only, metadata first, then the extras you chose;
4. **connects keygraph to itself** over MCP with the exact absolute paths that `keygraph doctor` prints, and adds standing instructions so that it uses `search`, `describe`, `path` and `keys` before it writes SQL;
5. **proves it works**: `keygraph doctor`, then a live `search` and `path` through MCP;
6. **writes a setup record** (`keygraph-out/SETUP.md`) with the rebuild command.

Your credentials never go into a config file: the MCP server only reads the built map folder.

## What is in this folder

| File | For |
|---|---|
| [SETUP.md](SETUP.md) | The protocol the agent follows, step by step |
| [harnesses/](harnesses) | Per-agent details: [Claude Code](harnesses/claude-code.md) (also with DeepSeek, z.ai GLM or other backends), [Codex](harnesses/codex.md), [Cursor](harnesses/cursor.md), [ZCode](harnesses/zcode.md), [OpenCode](harnesses/opencode.md), [Unsloth Studio and local models](harnesses/unsloth-studio.md), [everything else, including agents with no MCP](harnesses/other.md) |
| [templates/](templates) | The standing-instructions block, a Cursor rule, a business-context starter, the setup record |
| [BUG_REPORTS.md](BUG_REPORTS.md) | How an agent tells a keygraph bug from a local problem, redacts it, checks for duplicates and opens an issue only after you say yes |

## Check a setup any time

```bash
keygraph doctor --dir keygraph-out          # checks the install, the map and the MCP server; prints the MCP launch command
keygraph doctor --dir keygraph-out --report env.md   # a redacted environment block for a bug report
```
