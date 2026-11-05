# keygraph setup record

Written by an AI agent following https://github.com/alikarami76/keygraph/blob/main/setup/SETUP.md. A later setup reads this file and updates it in place.

- Set up on: <date>, keygraph <version> (from `keygraph doctor`)
- Harness: <Claude Code | Codex | Cursor | ZCode | OpenCode | Unsloth Studio | other>, MCP scope: <project | user>
- Map folder: <absolute OUT>
- Shape at setup: <N> tables, <N> relations, <N> schemas, <N> context entries

## Answers

| Question | Answer |
|---|---|
| Source | <live via $KEYGRAPH_DB_URL from <VAR NAME> in .env / dumps / command / demo> |
| Schemas | <list>; copies: <globs or none> |
| Extras | profiles <yes/no>, code index <yes/no>, docs import <yes/no>, query log <yes/no> |
| Starter context | <yes: keygraph-context.yaml (unverified) / no> |
| Bug reports | <draft and ask / never> |

## Rebuild (after schema changes)

```bash
<the exact commands used, with "$KEYGRAPH_DB_URL" and never the value>
keygraph render --dir <OUT>
keygraph doctor --dir <OUT>
```

## Installed files

- MCP: <config file or command used>
- Skill: <path>
- Instructions block: <file> (between the keygraph:start and keygraph:end markers)
