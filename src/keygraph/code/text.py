"""Plain-text output of the code index commands (the --json output is the dict itself)."""

from __future__ import annotations


def _ref_line(r: dict, show_table: bool = True) -> str:
    target = r["table"] + (f".{r['column']}" if r["column"] else "") if show_table else (r["column"] or "(table)")
    sym = f"  {r['symbol']}" if r.get("symbol") else ""
    return f"  {r['repo']}:{r['path']}:{r['line']}  {r['kind']:<14} {target}  {r['confidence']:.2f}  {r['rule']}{sym}"


def format_where(res: dict) -> str:
    target = res["table"] + (f".{res['column']}" if res["column"] else "")
    counts = ", ".join(f"{k} {n}" for k, n in res["counts"].items() if n)
    lines = [f"{target}: {res['total']} code refs" + (f" ({counts})" if counts else "")]
    lines += [_ref_line(r) for r in res["refs"]]
    if not res["refs"]:
        lines.append("  (none; index a repo with `keygraph code index REPO --name NAME`)")
    return "\n".join(lines)


def format_what(res: dict) -> str:
    if "symbol" in res:
        return "\n".join([f"symbol {res['symbol']}: {len(res['refs'])} refs"] + [_ref_line(r) for r in res["refs"]])
    if not res["files"]:
        return "no indexed file matches that path"
    out = []
    for f in res["files"]:
        out.append(f"{f['repo']}:{f['path']}" + (f":{f['line']}" if f["line"] is not None else "") + f": {len(f['refs'])} refs")
        out += [_ref_line(r) for r in f["refs"]]
        if f.get("entity"):
            e = f["entity"]
            out.append(f"  entity {e['symbol'] or ''} -> {e['table']} (declared on line {e['line']})")
            out += ["  " + _ref_line(r, show_table=False) for r in e["refs"] if r["kind"] == "declare_column"]
    return "\n".join(out)


def format_stats(res: dict) -> str:
    if not res["repos"]:
        return "no repos indexed; run `keygraph code index REPO --name NAME`"
    out = []
    for r in res["repos"]:
        kinds = ", ".join(f"{k} {n}" for k, n in r["refs_by_kind"].items())
        out.append(f"{r['repo']}: {r['files']} files, {r['refs']} refs ({kinds}), {r['unresolved']} unresolved, indexed {r['indexed_at']}")
        out += [f"  {t['table']}: {t['refs']}" for t in r["top_tables"]]
    return "\n".join(out)


def format_index(res: dict) -> str:
    capped = f"; uses capped at {res['max_uses']} for {len(res['capped'])} column(s)" if res["capped"] else ""
    return (f"{res['repo']}: {res['files']} files ({res['changed']} changed, {res['deleted']} deleted), {res['refs']} refs, "
            f"{res['unresolved']} unresolved{capped} ({res['seconds']}s)")
