"""Plain-text output of the queries commands (the --json output is the dict itself)."""

from __future__ import annotations


def _ms(v) -> str:
    return "-" if v is None else f"{v:,.0f} ms"


def _one(q: dict) -> list[str]:
    head = f"{q['fp']}  {q['runs']} runs, {q['people']} people, median {_ms(q['median_ms'])}, p95 {_ms(q['p95_ms'])}"
    if q.get("trend"):
        t = q["trend"]
        head += f", last 5 median {_ms(t['recent_median_ms'])} vs {_ms(t['earlier_median_ms'])} before"
    lines = [head]
    if q.get("context"):
        lines.append(f"  why: {q['context']}")
    lines.append(f"  sql: {q['normalized_sql'][:300]}")
    if q.get("roles"):
        lines.append(f"  roles: {', '.join(q['roles'])}")
    return lines


def format_list(title: str, rows: list[dict]) -> str:
    out = [title]
    for q in rows:
        out += _one(q)
    if not rows:
        out.append("  (none; log queries with `keygraph queries add` or `keygraph queries import`)")
    return "\n".join(out)


def format_show(q: dict) -> str:
    out = _one(q)
    out += [f"  {r['table']}" + (f".{r['column']}" if r["column"] else "") + f"  {r['role']}" for r in q["refs"]]
    for r in q["runs_list"]:
        status = "ok" if r["ok"] else f"error: {r['error']}"
        out.append(f"  run {r['id']}  {r['at']}  {r['source']}  {_ms(r['ms'])}  rows {r['rows'] if r['rows'] is not None else '-'}"
                   f"  {r['by'] or '-'}  {status}")
    return "\n".join(out)


def format_stats(s: dict) -> str:
    out = [f"{s['runs']} runs, {s['fingerprints']} distinct queries, {s['failed_runs']} failed, {s['tables']} tables touched"]
    out += [f"  source {k}: {v}" for k, v in s["sources"].items()]
    if s["unresolved"]:
        out.append("  unresolved tables: " + ", ".join(f"{u['name']} ({u['runs']})" for u in s["unresolved"][:20]))
    return "\n".join(out)
