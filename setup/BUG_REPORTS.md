# Reporting keygraph bugs (for AI agents)

Good reports from agents make keygraph better for everyone. This protocol keeps the user's data private and keeps reports short and useful. **Never send anything without the user's explicit yes.**

## Is it a keygraph bug?

| Report it | Do not report it (fix it locally) |
|---|---|
| A traceback or crash from any `keygraph` command or MCP tool | A connection or permission error from the user's database or credentials |
| `keygraph doctor` shows `fail` on a correct install | A missing extra (`doctor` prints the install command) |
| A declared foreign key is missing from the graph | An inferred relation below 0.8 that is wrong: reject it in an overrides file instead (see below for when to share it anyway) |
| `path` returns JOIN columns that do not exist, or a route through a relation the graph does not have | A bare table name that is ambiguous: use `schema.table` |
| An MCP tool's JSON differs from the CLI's `--json` | Questions about how to use keygraph: read the skill and README first |
| The docs or this setup protocol tell you to run something that does not work | |

**Inference quality.** If keygraph confidently (0.8 and above) infers a relation that is wrong, or misses an obvious one, that is worth reporting. Inference improves from real naming patterns. Ask the user before you include real table or column names. If they say no, describe the pattern with placeholders (`<t1>.<x>_ref -> <t2>.id`).

## Steps

1. **Reproduce in the smallest form.** One command or one tool call. Note the exact command, the expected result and the actual result.
2. **Collect the environment block:**
   ```bash
   keygraph doctor --dir "$OUT" --report keygraph-bug-env.md
   ```
   It contains versions, counts and check results only: no table names, SQL, values or home-directory paths.
3. **Write the report** with the template below, in a local file (for example `keygraph-bug.md`). Redact it:
   - never include credentials, connection URLs, host names, values from the data, or SQL with literals;
   - replace real table and column names with placeholders unless the user allowed names;
   - shorten tracebacks to the keygraph frames plus the final error line.
4. **Look for duplicates:**
   ```bash
   gh issue list -R alikarami76/keygraph --state all --search "<the error message or command>"
   ```
   If one matches, offer to add a comment with your environment block instead of opening a new issue.
5. **Show the user the exact text** and ask: "May I open this issue on github.com/alikarami76/keygraph?" Wait for a clear yes.
6. **Send it.**
   ```bash
   gh issue create -R alikarami76/keygraph --title "<short title>" --body-file keygraph-bug.md
   ```
   Without `gh`, give the user this link and the text to paste:
   `https://github.com/alikarami76/keygraph/issues/new?template=agent_bug_report.yml`
7. **Carry on.** Tell the user the issue link and the workaround you are using (for example `schema.table`, an overrides `reject`, or the CLI instead of MCP).

## Template

```markdown
**Summary:** <one sentence>

**Harness:** <Claude Code / Codex / Cursor / ZCode / OpenCode / Unsloth Studio / other> with <model>
**Source:** <postgres / mysql / sqlserver / sqlite / clickhouse / mongodb / dumps> via <scan / load / fetch / profile>

**Steps to reproduce**
1. <command or tool call, names redacted>

**Expected:** <what should happen>
**Actual:** <what happened, with the final error line>

<paste keygraph-bug-env.md here>
```
