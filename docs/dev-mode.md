# Dev mode

Dev mode records how agents use keygraph, so you can study what they asked and why keygraph answered as it did. It is off by default, it writes only local files inside the output directory, and it changes no output of keygraph.

Use it to answer questions such as: which tools do agents call, which calls are slow or fail, which searches find nothing, which table names are ambiguous, and which inference rule kept or rejected a relation.

## Turn it on and off

```bash
keygraph dev on --dir keygraph-out       # writes keygraph-out/dev/enabled
keygraph dev status --dir keygraph-out --json
keygraph dev off --dir keygraph-out      # stops logging, keeps the log
```

`keygraph dev status --json` prints `{"enabled", "source", "dir", "events_bytes", "trace_files"}`. `source` is `marker`, `env` or `off`.

The environment variable `KEYGRAPH_DEV` overrides the marker: `KEYGRAPH_DEV=1` forces dev mode on and `KEYGRAPH_DEV=0` forces it off, whatever `dev on` or `dev off` said. For an MCP client, put it in the server's environment (the optional `env` entry in the MCP config).

A running MCP server notices `dev on` and `dev off` on its next call, with no restart, and starts or stops logging. One thing needs a restart: the `dev_note` tool (below) is registered only if dev mode was on when the server started. After `keygraph dev on`, restart the MCP server so agents see `dev_note`.

## What is recorded

Everything goes under `<out>/dev/`.

- `events.jsonl`: one line per agent interaction: an MCP tool call, a resource read, a CLI command, or a note.
- `traces/<timestamp>-<name>.jsonl`: one file per build or long task (`load`, `scan`, `render`, `profile`, `probe`, `code index`, `queries import`). A trace holds phase timings, every inference candidate for every column (kept or rejected, with the reason), override, mirror and hub decisions.
- Decisions on calls: an event for a query-time call carries `decisions` (the rule that resolved a table name, search score contributions per table, path candidates with their penalties and why the winner won), at most 200 entries.

An event looks like this (a CLI note, then a `path` command, shortened; real events are one line each and the keys are sorted):

```json
{"v": 1, "ts": "2026-11-13T14:43:21.707+00:00", "session": "f7b519a42ffd", "seq": 1, "surface": "cli", "client": {},
 "kind": "note", "tool": "dev_note", "args": {"question": "which customers have unpaid orders?"}, "ms": 0.0, "ok": true}
{"v": 1, "ts": "2026-11-13T14:43:21.996+00:00", "session": "demo1", "seq": 1, "surface": "cli", "client": {},
 "kind": "command", "tool": "path",
 "args": {"a": "customers", "b": "shipments", "k": 3, "max_hops": 4, "min_confidence": 0.6, "exit_status": 0},
 "ms": 2.4, "ok": true,
 "response": {"from": "shop.customers", "to": "shop.shipments", "paths": ["..."]},
 "decisions": [{"kind": "resolve", "name": "customers", "rule": "original_over_mirror_or_copy", "table": "shop.customers"}, "..."]}
```

Fields: `v` (schema version), `ts`, `session`, `session_source`, `seq`, `surface` (`mcp` or `cli`), `client` (name and version, when the MCP client sends them), `kind` (`call`, `resource`, `command`, `note`), `tool`, `args`, `ms`, `ok`, `error` (`type`, `message`), `response` or `response_ref`, `decisions`, `trace`.

Responses are stored inline up to 16 KB. A larger response is stored as a hash, its size in bytes, its approximate tokens and its shape (counts of lists and keys). Approximate tokens are the character count divided by 4. Some CLI commands that print text are stored as a reference only, even when small.

Sessions: one MCP server process is one session. For CLI commands the log records the id and where it came from, in `session_source`: `env` (`KEYGRAPH_DEV_SESSION` is set), `ppid` (a hash of the parent process id and the output directory) or `mcp`. Agents usually run each tool call in a new shell, so `ppid` ids split one agent run into many. The report therefore regroups CLI events whose source is `ppid` (and older events with no `session_source`) into report sessions: consecutive events in the dev dir belong together until 10 minutes pass with no command, then a new session starts. Report session ids look like `cli-<hash>` and are the same for the same logs. A `dev note` from the CLI joins the regrouped session. Sections 1, 7 and 9 and `dev report --session` use report sessions; the raw log keeps the original id. Sessions with a `KEYGRAPH_DEV_SESSION` are never regrouped, so setting it is still the precise option (put it in front of each command, or `export` it if your shell keeps state).

File modes: `<out>/dev/` and `traces/` are created with mode 0700 and `events.jsonl` and trace files with 0600 (POSIX; best effort on Windows).

Size limits: `events.jsonl` rotates at 20 MB and keeps five older files (`events.1.jsonl` to `events.5.jsonl`). A trace stops at 50 MB or 200,000 events and ends with a `truncated` event. Only the newest 20 traces are kept.

## What is never recorded

- Database row values. Profile values (top values, ranges, examples) are replaced by `"<masked>"` in logged responses, including `describe` and `search` answers.
- SQL literals. SQL in arguments goes through the same masking as the query log.
- Secrets: `password=`, `token=`, `api_key=` style values, AWS and GitHub token formats, JWTs, emails and phone-like numbers (also after `Bearer`, and after `Basic`, `Token` or `Digest` in an `Authorization:` header, and with dotted keys such as `conn.password=`) are masked in every logged string. Very long unbroken strings are replaced by a length marker.
- Connection URLs: any `scheme://...` value is stored as `scheme://***`.
- `--cmd` templates: stored as `<program> <redacted-cmd>`.
- Tools that return database rows log only the shape of the result, never the rows.

## What is recorded, and you should know it

- Table and column names, in arguments, answers and traces. They can reveal your business.
- Text the agent wrote: search queries, the `context` of `query_submit`, and notes. This is the point of the log. Secrets, emails and phone numbers in it are masked, but nothing else is, so read an export before you share it.

## The limit: keygraph never sees the conversation

keygraph sees only tool calls. It does not see what the user asked or what the agent said. To attach the user's question, the agent calls `dev_note`:

- With MCP, when the `dev_note` tool is listed, the agent calls it once per user question, before its first keygraph call for that question, with the question in the user's words and no personal data. When dev mode is off the tool is not listed and the agent does nothing.
- Without MCP, the agent runs `keygraph dev note --dir <out> "<question>"` (add `--json` for JSON; use `--` before text that starts with `-`). With `--json` it prints the session id; the report joins the later commands to that question by time (10-minute idle gap), and the agent may put `KEYGRAPH_DEV_SESSION=<session>` in front of its later commands for exact grouping. When dev mode is off, `dev note` records nothing and says so.

Both return `{"noted", "session", "question", "hint"}`, plus a `reason` when `noted` is false (`dev mode is off` or `write failed`). Nothing was written then. Do not retry.

## Read the log

```bash
keygraph dev report --dir keygraph-out                       # markdown
keygraph dev report --dir keygraph-out --json                # the same data as JSON
keygraph dev report --dir keygraph-out --since 2026-11-12    # a date or a time (UTC unless it has an offset)
keygraph dev report --dir keygraph-out --session demo1       # one session
```

The report is deterministic: the same logs give the same output, with no "generated at" line. Sections:

1. Overview: period, event counts, sessions, clients.
2. Most-used tools and commands.
3. Latency per tool (p50, p95, max) and the 10 slowest calls.
4. Errors, grouped by tool and message.
5. Empty answers: searches with no tables, paths with none, code and query lookups with nothing found.
6. Ambiguous answers: ambiguous or unknown table names, and paths whose first answer uses an ambiguous edge.
7. Repeated calls (same tool and arguments twice in a session) and retry chains.
8. Response size per tool (mean and max approximate tokens).
9. Agent notes, next to the calls that followed them.
10. Latest build: phase timings, inference candidates kept and rejected per heuristic and reason, unresolved and polymorphic columns, mirror and hub decisions.

A final part, "Decisions seen at query time", counts name-resolution rules and path winner reasons.

Excerpt from a real run on the demo (a note, two searches, one with no hits, and the same path asked twice):

```text
## 5. Empty answers

| tool | empty | meaning | examples |
|---|---|---|---|
| search | 1 | no tables | `{"json": true, "limit": 8, "no_paths": false, "query": ["zzzqq nothing"]}` |

## 7. Repeated calls

| session | tool | args | count |
|---|---|---|---|
| demo1 | path | `{"a": "customers", "b": "shipments", "include_mirrors": false, "json": true, ...}` | 2 |

## 9. Agent notes

- 2026-11-13T14:43:21.707+00:00 (session f7b519a42ffd): which customers have unpaid orders?
  - 0 calls followed
```

## Share, export, clear

```bash
keygraph dev export dev-log.jsonl.gz --dir keygraph-out [--since 2026-11-12]
keygraph dev clear --dir keygraph-out          # delete events and traces, keep the on marker
keygraph dev clear --dir keygraph-out --all    # also remove the marker (dev mode off)
```

`dev export FILE` writes every event and trace line to one file (a name ending in `.gz` is gzipped; the file is private to you, mode 0600). It refuses a target inside `<out>/dev/`. It prints a warning because the file holds table names and agent-written text. Read it before you send it to anyone.

The `dev` folder sits inside the output directory, which `.gitignore` usually excludes. Keep it out of version control.

## Overhead

Build time (`keygraph load`, in process, median of repeated runs, measured 2026-11-13): dev off is within noise of the baseline. Dev on adds about 10% on the demo (0.05 s) and about 2x on a 2,000-table synthetic schema, because the build trace records every inference candidate.

Query-time latency (MCP tools called in process through `keygraph.mcp_server.build_server`, 200 calls per tool after a warm-up, five interleaved rounds of separate processes, so 1,000 calls per cell; demo map; Apple M4, Python 3.12; measured 2026-11-13). Median / p95 in milliseconds:

| tool | main (8ac284b) | this branch, dev off | this branch, dev on |
|---|---|---|---|
| search | 0.865 / 1.129 | 0.872 / 1.041 | 1.761 / 2.067 |
| describe | 0.357 / 0.419 | 0.354 / 0.439 | 1.325 / 1.456 |
| path | 0.236 / 0.255 | 0.242 / 0.265 | 0.972 / 1.282 |
| neighbors | 0.145 / 0.153 | 0.146 / 0.153 | 1.114 / 1.436 |

With dev off the latency matches main within noise. With dev on every call costs about 0.9 to 1.0 ms more (an event is written to disk per call, plus masking and hashing), which is 2x on `search` and 4 to 8x on the cheapest tools. That is small next to an agent round trip, but it is not free: do not leave dev mode on where you measure keygraph's own speed.

Logging never breaks a tool: if a write fails, the tool still answers and one line goes to stderr.
