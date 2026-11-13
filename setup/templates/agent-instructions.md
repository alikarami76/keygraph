<!-- keygraph:start (managed by keygraph setup; edit between the markers, a later setup replaces this block) -->
## Database work: use keygraph first

This project's database is mapped by keygraph (map folder: `{OUT}`). It is connected as the `keygraph` MCP server. Without MCP, use `keygraph <command> --json --dir {OUT}`.

Before you write or change SQL that touches more than one table, or when you do not know where some data lives:

1. **Find:** `search` with the business words of the question (or `areas`, then `area`).
2. **Understand:** `describe` each candidate table: what one row is, the keys, status codes, pitfalls. For a first look at a wide table, use `describe` with `brief=true` (CLI `--brief`).
3. **Join:** `path` between every pair of tables you join. Use its JOIN columns. Never guess join columns from names.
4. **Filter:** `keys` on large tables, and filter on the leading column of an index.
5. **Reuse:** `query_for` / `query_search` for queries that worked before.

Read confidence honestly. Declared keys are 1.0. Use inferred relations at 0.8 and above. Check 0.6 to 0.8 against the evidence. Verify anything lower with a small SELECT before you rely on it. A `fan-out` hop multiplies rows, so aggregate before that join.

After the work:

- Log a query that answered a business question with `query_submit`.
- Save a fact you confirmed (what a row is, what a status code means) with `keygraph context add --kind column --author "<your agent name>" table=<schema.table> column=<column> meaning="..." verified=true --dir {OUT}` (`--kind` is required; set `KEYGRAPH_AUTHOR` once instead of `--author`; `verified=true` only for what you checked against data or code).
- If keygraph errors or gives a wrong answer, follow https://github.com/alikarami76/keygraph/blob/main/setup/BUG_REPORTS.md. Never send a report without the user's yes.

keygraph never runs SQL and never writes to the database. Run read-only queries with your own access.
<!-- keygraph:end -->
