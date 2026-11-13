"""Dev mode report and export (stdlib only): what agents did with keygraph, read from <out>/dev.

`report(out)` returns a dict and `format_report(dict)` renders it as markdown. Both are deterministic: the same logs give the
same bytes (sorted keys and lists, stable tie-breaks, no clock; times appear only as the logs wrote them).
`export(out, path)` writes every event and trace line, tagged with its source file, to one file (.gz is gzipped).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import gzip
import json
import math
import os
import re
from pathlib import Path

from . import devlog

CALL_KINDS = {"call", "command", "resource"}
UNIT = "approx tokens = chars/4"
SHORT = 120
_ROTATED = re.compile(r"events\.(\d+)\.jsonl$")
_QUOTED = re.compile(r"'[^'\s][^']*'")
_DOTTED = re.compile(r"\b[^\W\d]\w*\.[^\W\d]\w*\b")
# list keys that mean "the answer had something in it", per tool (CLI names map onto the MCP names)
_ALIASES = {"code where": "code_where", "code what": "code_what", "queries for": "query_for", "queries top": "query_top",
            "queries search": "query_search"}
_EMPTY_LISTS = {"search": ("tables",), "path": ("paths",), "code_where": ("refs",), "code_what": ("refs", "files"),
                "query_for": ("queries",), "query_top": ("queries",), "query_search": ("queries",)}
_EMPTY_WHAT = {"search": "no tables", "path": "no paths", "neighbors": "no neighbors", "code_where": "no references",
               "code_what": "nothing found", "query_for": "no logged queries", "query_top": "no logged queries",
               "query_search": "no logged queries", "describe": "no relations (isolated table)",
               "profile_summary": "no stored profiles"}


# ---- reading ----------------------------------------------------------------------------------------------

def _utc(s: str) -> dt.datetime:
    """An ISO date or time as an aware UTC datetime (no offset means UTC, which is how the logs are written)."""
    t = dt.datetime.fromisoformat(s.strip())
    try:
        return t.replace(tzinfo=dt.UTC) if t.tzinfo is None else t.astimezone(dt.UTC)
    except OverflowError:  # an offset that pushes the time outside year 1..9999
        raise ValueError(f"time out of range: {s!r}") from None


def _norm_since(since) -> dt.datetime | None:
    if since is None:
        return None
    try:
        return _utc(str(since))
    except ValueError:
        raise ValueError(f"--since wants an ISO date or time such as 2026-11-12, 2026-11-12T09:30 or 2026-11-12T09:30+03:30, got {since!r}") from None


def _trace_stamp(since: dt.datetime) -> str:
    """The `<YYYYmmddTHHMMSSffffff>` UTC prefix of trace file names, for comparing with a --since value."""
    return since.strftime("%Y%m%dT%H%M%S%f")


def _event_files(d: Path) -> list[Path]:
    rot = sorted((p for p in d.glob("events.*.jsonl") if _ROTATED.search(p.name)), key=lambda p: -int(_ROTATED.search(p.name).group(1)))
    live = d / "events.jsonl"
    return rot + ([live] if live.exists() else [])


def _trace_files(d: Path) -> list[Path]:
    return sorted((d / "traces").glob("*.jsonl"), key=lambda p: p.name) if (d / "traces").is_dir() else []


def _lines(path: Path):
    """(line number, object, raw text) per non-blank line; object is None for a corrupt line (not JSON, or not an object)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for n, raw in enumerate(text.split("\n"), 1):  # not splitlines(): U+2028, U+2029 and U+0085 can sit inside an event
        raw = raw.removesuffix("\r")
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        yield n, obj if isinstance(obj, dict) else None, raw


def _keep(e: dict, since: dt.datetime | None, session: str | None) -> bool:
    if since is not None:
        try:
            if not (isinstance(e.get("ts"), str) and _utc(e["ts"]) >= since):
                return False
        except ValueError:
            return False
    return session is None or e.get("session") == session


def _load(out, since: dt.datetime | None, session: str | None) -> tuple[list[dict], list[tuple[str, list[dict]]], int]:
    """(events sorted by time, [(trace name, events)], corrupt line count)."""
    d = devlog.dev_dir(out)
    events, traces, corrupt = [], [], 0
    for p in _event_files(d):
        for _, obj, _raw in _lines(p):
            if obj is None:
                corrupt += 1
            elif _keep(obj, since, session):
                events.append(obj)
    events.sort(key=lambda e: (str(e.get("ts", "")), str(e.get("session", "")), _num(e.get("seq"))))
    linked = {e.get("trace") for e in events if e.get("trace")}
    stamp = _trace_stamp(since) if since else None
    for p in _trace_files(d):
        evs = []
        for _, obj, _raw in _lines(p):
            if obj is None:
                corrupt += 1
            else:
                evs.append(obj)
        if (stamp is None or p.name[:21] >= stamp) and (session is None or p.stem in linked):
            traces.append((p.stem, evs))
    return events, traces, corrupt


# ---- small helpers ----------------------------------------------------------------------------------------

def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _s(v) -> str:
    return v if isinstance(v, str) else "" if v is None else str(v)


def _args(e: dict) -> dict:
    a = e.get("args")
    return {k: v for k, v in a.items() if k != "exit_status"} if isinstance(a, dict) else {}


def _canon(e: dict) -> str:
    return json.dumps(_args(e), sort_keys=True, ensure_ascii=False, default=str)


def _short(text: str) -> str:  # the log was masked when it was written; masking again would only garble numbers
    return text if len(text) <= SHORT else text[:SHORT - 1] + "…"


def _tool(e: dict) -> str:
    return _s(e.get("tool")) or "?"


def _error(e: dict) -> tuple[str, str]:
    er = e.get("error")
    if isinstance(er, dict):
        return _s(er.get("type")), _s(er.get("message"))
    return "", _s(er)


def _pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[max(0, math.ceil(p * len(s)) - 1)]


def _decisions(e: dict) -> list[dict]:
    d = e.get("decisions")
    return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []


def _list_count(e: dict, key: str) -> int | None:
    """How many items a response list holds: from the stored response, or from the shape of a stored reference."""
    r = e.get("response")
    if isinstance(r, dict):
        v = r.get(key)
        return len(v) if isinstance(v, list) else None
    ref = e.get("response_ref")
    if isinstance(ref, dict) and isinstance(ref.get("shape"), dict):
        lists = ref["shape"].get("lists")
        if isinstance(lists, dict) and isinstance(lists.get(key), int):
            return lists[key]
        if key == "tables" and isinstance(ref["shape"].get("tables"), list):
            return len(ref["shape"]["tables"])
        if key == "paths" and isinstance(ref["shape"].get("paths"), int):
            return ref["shape"]["paths"]
    return None


def _is_empty(tool: str, e: dict) -> bool:
    """True when a successful answer held nothing, by what the tool returns (False when it cannot be told)."""
    tool = _ALIASES.get(tool, tool)
    r = e.get("response")
    if tool in _EMPTY_LISTS:
        counts = [c for c in (_list_count(e, k) for k in _EMPTY_LISTS[tool]) if c is not None]
        if counts:
            return counts[0] == 0
        return tool == "path" and isinstance(r, str) and r.startswith("No join path found")
    if tool == "neighbors" and isinstance(r, dict) and isinstance(r.get("levels"), dict):
        return not any(v for v in r["levels"].values())
    if tool == "describe" and isinstance(r, dict) and isinstance(r.get("relations"), list):
        return not r["relations"]
    if tool == "profile_summary" and isinstance(r, dict) and isinstance(r.get("columns"), dict):
        return not r["columns"]
    return False


def _tokens(e: dict) -> int | None:
    ref = e.get("response_ref")
    if isinstance(ref, dict):
        return int(_num(ref.get("tokens_approx")))
    if "response" in e:
        r = e["response"]
        text = r if isinstance(r, str) else json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
        return len(text) // 4
    return None


def _top(counter: dict, key=None) -> list:
    return sorted(counter.items(), key=key or (lambda kv: (-kv[1], str(kv[0]))))


# ---- sections ---------------------------------------------------------------------------------------------

def _overview(events: list[dict], traces, corrupt: int, out) -> dict:
    kinds: dict[str, int] = {}
    clients: dict[tuple[str, str], int] = {}
    surfaces: dict[str, int] = {}
    stamps = [e["ts"] for e in events if isinstance(e.get("ts"), str)]
    for e in events:
        kinds[_s(e.get("kind")) or "?"] = kinds.get(_s(e.get("kind")) or "?", 0) + 1
        c = e.get("client")
        if isinstance(c, dict) and c.get("name"):
            k = (_s(c.get("name")), _s(c.get("version")))
            clients[k] = clients.get(k, 0) + 1
        if e.get("surface"):
            surfaces[_s(e["surface"])] = surfaces.get(_s(e["surface"]), 0) + 1
    return {"events": len(events), "kinds": dict(sorted(kinds.items())),
            "sessions": len({_s(e.get("session")) for e in events}),
            "period": {"first": min(stamps), "last": max(stamps)} if stamps else None,
            "clients": [{"name": n, "version": v, "events": c} for (n, v), c in sorted(clients.items())],
            "surfaces": dict(sorted(surfaces.items())), "corrupt_lines": corrupt, "trace_files": len(traces),
            "event_files": len(_event_files(devlog.dev_dir(out)))}


def _usage(calls: list[dict]) -> list[dict]:
    n: dict[str, int] = {}
    for e in calls:
        n[_tool(e)] = n.get(_tool(e), 0) + 1
    return [{"tool": t, "count": c, "share": round(100 * c / len(calls), 1)} for t, c in _top(n)]


def _latency(calls: list[dict]) -> dict:
    per: dict[str, list[float]] = {}
    for e in calls:
        per.setdefault(_tool(e), []).append(round(_num(e.get("ms")), 2))
    rows = [{"tool": t, "n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "max": max(v)} for t, v in sorted(per.items())]
    slow = sorted(calls, key=lambda e: (-_num(e.get("ms")), str(e.get("ts", "")), _tool(e), _canon(e), str(e.get("session", ""))))[:10]
    return {"per_tool": rows, "slowest": [{"tool": _tool(e), "ms": round(_num(e.get("ms")), 2), "args": _short(_canon(e)),
                                           "ts": _s(e.get("ts")), "session": _s(e.get("session"))} for e in slow]}


def _errors(calls: list[dict]) -> list[dict]:
    g: dict[tuple, int] = {}
    for e in calls:
        if e.get("ok") is False:
            k = (_tool(e), *_error(e))
            g[k] = g.get(k, 0) + 1
    return [{"tool": t, "type": ty, "message": m, "count": c} for (t, ty, m), c in _top(g)]


def _empty(calls: list[dict]) -> list[dict]:
    g: dict[str, list[str]] = {}
    for e in calls:
        if e.get("ok") is not False and _is_empty(_tool(e), e):
            g.setdefault(_ALIASES.get(_tool(e), _tool(e)), []).append(_short(_canon(e)))
    rows = [{"tool": t, "count": len(a), "what": _EMPTY_WHAT.get(t, "nothing"), "examples": list(dict.fromkeys(a))[:3]}
            for t, a in g.items()]
    return sorted(rows, key=lambda r: (-r["count"], r["tool"]))


def _ambiguous(calls: list[dict]) -> dict:
    res: dict[tuple, dict] = {}
    paths: dict[tuple, dict] = {}
    for e in calls:
        for d in _decisions(e):
            if d.get("kind") == "resolve" and d.get("rule") in ("ambiguous", "unknown"):
                cands = d.get("candidates") if d["rule"] == "ambiguous" else d.get("suggestions")
                cands = sorted(_s(c) for c in cands) if isinstance(cands, list) else []
                r = res.setdefault((d["rule"], _s(d.get("name")), tuple(cands)),
                                   {"rule": d["rule"], "name": _s(d.get("name")), "candidates": cands, "count": 0, "tools": set()})
                r["count"] += 1
                r["tools"].add(_tool(e))
        r = e.get("response")
        if _tool(e) == "path" and e.get("ok") is not False and isinstance(r, dict) and isinstance(r.get("paths"), list) and r["paths"]:
            first = r["paths"][0]
            steps = first.get("steps") if isinstance(first, dict) else None
            edges = [{"from": _s(s.get("from")), "to": _s(s.get("to"))} for s in steps or [] if isinstance(s, dict) and s.get("ambiguous")]
            if edges:
                k = tuple((x["from"], x["to"]) for x in edges)
                p = paths.setdefault(k, {"edges": edges, "count": 0, "example": _short(_canon(e))})
                p["count"] += 1
    rrows = sorted(({**v, "tools": sorted(v["tools"])} for v in res.values()), key=lambda v: (-v["count"], v["rule"], v["name"]))
    return {"resolve": rrows, "paths": sorted(paths.values(), key=lambda v: (-v["count"], json.dumps(v["edges"])))}


def _by_session(calls: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in calls:
        out.setdefault(_s(e.get("session")), []).append(e)
    return dict(sorted(out.items()))


def _repeated(calls: list[dict]) -> dict:
    same: dict[tuple, int] = {}
    retries = []
    for sess, cs in _by_session(calls).items():
        for e in cs:
            k = (sess, _tool(e), _canon(e))
            same[k] = same.get(k, 0) + 1
        for i, e in enumerate(cs):
            if e.get("ok") is not False:
                continue
            for j in range(i + 1, min(i + 4, len(cs))):
                if _tool(cs[j]) == _tool(e) and _canon(cs[j]) != _canon(e):
                    retries.append({"session": sess, "tool": _tool(e), "failed_args": _short(_canon(e)), "retry_args": _short(_canon(cs[j])),
                                    "calls_between": j - i - 1, "retry_ok": cs[j].get("ok") is not False})
                    break
    rows = [{"session": s, "tool": t, "args": _short(a), "count": c} for (s, t, a), c in same.items() if c >= 2]
    rows.sort(key=lambda r: (-r["count"], r["tool"], r["args"], r["session"]))
    retries.sort(key=lambda r: (r["session"], r["tool"], r["failed_args"], r["retry_args"]))
    return {"same_args": rows, "retries": retries}


def _size(calls: list[dict]) -> dict:
    per: dict[str, list[int]] = {}
    refs: dict[str, int] = {}
    for e in calls:
        t = _tokens(e)
        if t is not None:
            per.setdefault(_tool(e), []).append(t)
            refs[_tool(e)] = refs.get(_tool(e), 0) + (1 if isinstance(e.get("response_ref"), dict) else 0)
    rows = [{"tool": t, "n": len(v), "mean": round(sum(v) / len(v), 1), "max": max(v), "stored_as_reference": refs[t]}
            for t, v in per.items()]
    return {"unit": UNIT, "per_tool": sorted(rows, key=lambda r: (-r["mean"], r["tool"]))}


def _notes(events: list[dict]) -> list[dict]:
    notes: list[dict] = []
    current: dict[str, dict] = {}
    for e in events:  # already in time order
        sess = _s(e.get("session"))
        if e.get("kind") == "note":
            q = _args(e).get("question")
            current[sess] = {"ts": _s(e.get("ts")), "session": sess, "question": _s(q), "call_count": 0, "calls": []}
            notes.append(current[sess])
        elif e.get("kind") in CALL_KINDS and sess in current:
            n = current[sess]
            n["call_count"] += 1
            if len(n["calls"]) < 20:
                n["calls"].append({"tool": _tool(e), "args": _short(_canon(e)), "ok": e.get("ok") is not False})
    return sorted(notes, key=lambda n: (n["ts"], n["session"], n["question"]))


def _reason(text) -> str:
    """A heuristic or rejection reason without the names in it, so the same reason groups together."""
    return _DOTTED.sub("<table>", _QUOTED.sub("'…'", _s(text)))


def _build(traces: list[tuple[str, list[dict]]]) -> dict | None:
    if not traces:
        return None
    pick = next((t for t in reversed(traces) if any(_s(e.get("kind")).startswith("infer.") for e in t[1])), traces[-1])
    name, evs = pick
    kept: dict[str, int] = {}
    rej: dict[str, int] = {}
    rej_h: dict[str, int] = {}
    unresolved = poly = 0
    phases, mirrors, hubs, verdicts, summary = [], [], [], {}, {}
    for e in evs:
        k = _s(e.get("kind"))
        if k == "phase":
            phases.append({a: b for a, b in e.items() if a != "kind"})
        elif k == "infer.column":
            unresolved += e.get("outcome") == "unresolved"
            poly += e.get("outcome") == "polymorphic"
            for c in e.get("candidates") or []:
                if not isinstance(c, dict):
                    continue
                reasons = [_reason(r) for r in c.get("reasons") or []] or ["(no reason recorded)"]
                if c.get("outcome") == "kept":
                    for r in reasons:
                        kept[r] = kept.get(r, 0) + 1
                else:
                    why = _reason(c.get("reason")) or "(no reason recorded)"
                    rej[why] = rej.get(why, 0) + 1
                    for r in reasons:
                        rej_h[r] = rej_h.get(r, 0) + 1
        elif k == "infer.summary":
            summary = {a: b for a, b in e.items() if a != "kind"}
        elif k == "mirror.decision":
            mirrors.append({a: e.get(a) for a in ("original", "copy", "verdict", "reason", "close_call") if a in e})
        elif k == "hub.decision":
            v = _s(e.get("verdict"))
            verdicts[v] = verdicts.get(v, 0) + 1
            if v != "not a hub":
                hubs.append({a: e.get(a) for a in ("table", "incoming", "verdict", "reason") if a in e})
    return {"trace": name, "phases": phases, "summary": summary, "unresolved_columns": unresolved, "polymorphic_columns": poly,
            "candidates": {"kept": dict(sorted(kept.items())), "rejected": dict(sorted(rej.items())),
                           "rejected_heuristics": dict(sorted(rej_h.items()))},
            "mirror": sorted(mirrors, key=lambda m: (_s(m.get("original")), _s(m.get("copy")))),
            "hubs": sorted(hubs, key=lambda h: _s(h.get("table"))), "hub_verdicts": dict(sorted(verdicts.items())),
            "truncated": any(e.get("kind") == "truncated" for e in evs)}


def _decision_summary(calls: list[dict]) -> dict:
    rules: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for e in calls:
        for d in _decisions(e):
            if d.get("kind") == "resolve":
                rules[_s(d.get("rule"))] = rules.get(_s(d.get("rule")), 0) + 1
            elif d.get("kind") == "path.rank" and d.get("winner_reason"):
                r = _s(d["winner_reason"]).split(" (")[0]
                reasons[r] = reasons.get(r, 0) + 1
    return {"resolve_rules": dict(sorted(rules.items())), "path_winner_reasons": dict(sorted(reasons.items()))}


def report(out, since: str | None = None, session: str | None = None) -> dict:
    """The dev report for <out>/dev as a dict. `since` is an ISO date or time; `session` keeps one session's events."""
    since = _norm_since(since)
    events, traces, corrupt = _load(out, since, session)
    calls = [e for e in events if e.get("kind") in CALL_KINDS]
    return {"overview": _overview(events, traces, corrupt, out), "usage": _usage(calls), "latency": _latency(calls),
            "errors": _errors(calls), "empty": _empty(calls), "ambiguous": _ambiguous(calls), "repeated": _repeated(calls),
            "size": _size(calls), "notes": _notes(events), "build": _build(traces), "decisions": _decision_summary(calls)}


# ---- markdown ---------------------------------------------------------------------------------------------

def _cell(v) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def _table(head: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["None."]
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]


def _counts(d: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in d.items()) or "none"


def format_report(r: dict) -> str:
    """The report dict as markdown."""
    o = r["overview"]
    if not o["events"]:
        return "# keygraph dev report\n\nno events\n"
    L: list[str] = ["# keygraph dev report", ""]
    p = o["period"]
    L += ["## 1. Overview", "", f"- Period: {p['first']} to {p['last']}" if p else "- Period: unknown",
          f"- Events: {o['events']} ({_counts(o['kinds'])})", f"- Sessions: {o['sessions']}",
          "- Clients: " + (", ".join(f"{c['name']} {c['version']} ({c['events']})".replace("  ", " ") for c in o["clients"]) or "none recorded"),
          f"- Surfaces: {_counts(o['surfaces'])}", f"- Corrupt lines skipped: {o['corrupt_lines']}",
          f"- Event files: {o['event_files']}, trace files: {o['trace_files']}", ""]
    L += ["## 2. Most-used tools and commands", ""] + _table(["tool", "count", "share %"], [[u["tool"], u["count"], u["share"]] for u in r["usage"]]) + [""]
    lat = r["latency"]
    L += ["## 3. Latency per tool", "", *_table(["tool", "calls", "p50 ms", "p95 ms", "max ms"],
                                               [[x["tool"], x["n"], x["p50"], x["p95"], x["max"]] for x in lat["per_tool"]]),
          "", "Slowest calls:", "", *_table(["tool", "ms", "args"], [[s["tool"], s["ms"], f"`{s['args']}`"] for s in lat["slowest"]]), ""]
    L += ["## 4. Errors", "", *_table(["tool", "type", "message", "count"], [[e["tool"], e["type"], e["message"], e["count"]] for e in r["errors"]]), ""]
    L += ["## 5. Empty answers", "", "Answers the CLI printed as text (no `--json`) are not classified, except `path`.", "", *_table(["tool", "empty", "meaning", "examples"],
                                            [[e["tool"], e["count"], e["what"], "; ".join(f"`{x}`" for x in e["examples"])] for e in r["empty"]]), ""]
    a = r["ambiguous"]
    L += ["## 6. Ambiguous answers", "", "Name resolution (ambiguous names and unknown tables, with candidates or suggestions):", "",
          *_table(["rule", "name", "candidates", "count", "tools"],
                  [[x["rule"], x["name"], ", ".join(x["candidates"]), x["count"], ", ".join(x["tools"])] for x in a["resolve"]]),
          "", "Paths whose first answer uses an ambiguous edge:", "",
          *_table(["edges", "count", "example"], [[", ".join(f"{e['from']} -> {e['to']}" for e in x["edges"]), x["count"], f"`{x['example']}`"]
                                                  for x in a["paths"]]), ""]
    rp = r["repeated"]
    L += ["## 7. Repeated calls", "", "Same tool and arguments twice or more in one session:", "",
          *_table(["session", "tool", "args", "count"], [[x["session"], x["tool"], f"`{x['args']}`", x["count"]] for x in rp["same_args"]]),
          "", "Retry chains (an error, then the same tool with different arguments within 3 calls):", "",
          *_table(["session", "tool", "failed args", "retry args", "calls between", "retry ok"],
                  [[x["session"], x["tool"], f"`{x['failed_args']}`", f"`{x['retry_args']}`", x["calls_between"], x["retry_ok"]] for x in rp["retries"]]), ""]
    L += ["## 8. Response size per tool", "", f"Size is {r['size']['unit']}.", "",
          *_table(["tool", "answers", "mean", "max", "stored as reference"],
                  [[x["tool"], x["n"], x["mean"], x["max"], x["stored_as_reference"]] for x in r["size"]["per_tool"]]), ""]
    L += ["## 9. Agent notes", ""]
    if not r["notes"]:
        L += ["None."]
    for n in r["notes"]:
        L += [f"- {n['ts']} (session {n['session']}): {n['question']}", f"  - {n['call_count']} calls followed"
              + (": " + "; ".join(f"{c['tool']} `{c['args']}`" + ("" if c["ok"] else " (error)") for c in n["calls"]) if n["calls"] else "")]
    L += [""]
    L += ["## 10. Latest build", ""]
    b = r["build"]
    if not b:
        L += ["No build trace (turn dev mode on before `keygraph load` or `keygraph scan`)."]
    else:
        L += [f"Trace: {b['trace']}" + (" (truncated at the trace cap)" if b["truncated"] else ""), "", "Phases:", "",
              *_table(["phase", "ms", "counts"], [[x.get("name"), x.get("ms"), ", ".join(f"{k}={v}" for k, v in sorted(x.items()) if k not in ("name", "ms"))]
                                                 for x in b["phases"]]),
              "", f"Unresolved columns: {b['unresolved_columns']}; polymorphic columns: {b['polymorphic_columns']}", "",
              "Candidates kept, by heuristic reason:", "", *_table(["reason", "count"], list(b["candidates"]["kept"].items())),
              "", "Candidates rejected, by rejection reason:", "", *_table(["reason", "count"], list(b["candidates"]["rejected"].items())),
              "", "Candidates rejected, by heuristic reason:", "", *_table(["reason", "count"], list(b["candidates"]["rejected_heuristics"].items())),
              "", "Mirror decisions:", "", *_table(["original", "copy", "verdict", "reason"],
                                                  [[m.get("original"), m.get("copy"), m.get("verdict"), m.get("reason")] for m in b["mirror"]]),
              "", f"Hub verdicts: {_counts(b['hub_verdicts'])}", "", *_table(["table", "incoming", "verdict", "reason"],
                                                                           [[h.get("table"), h.get("incoming"), h.get("verdict"), h.get("reason")] for h in b["hubs"]])]
    L += ["", "## Decisions seen at query time", "", f"- Name resolution rules: {_counts(r['decisions']['resolve_rules'])}",
          f"- Path winner reasons: {_counts(r['decisions']['path_winner_reasons'])}", ""]
    return "\n".join(L)


# ---- export -----------------------------------------------------------------------------------------------

WARNING = ("warning: the export holds table names and agent-written text (secrets and values are masked, but check it); "
           "share it with care")


def export(out, path, since: str | None = None) -> dict:
    """Write every event and trace line, in order, each tagged with its source file, behind a header line.
    Returns {"lines", "files", "path"}; nothing is written when there are no events."""
    from . import __version__
    since = _norm_since(since)
    d = devlog.dev_dir(out)
    stamp = _trace_stamp(since) if since else None
    items: list[tuple[str, int, dict | None, str]] = []
    for p in _event_files(d):
        for n, obj, raw in _lines(p):
            if since is None or (obj is not None and _keep(obj, since, None)):
                items.append((p.name, n, obj, raw))
    for p in _trace_files(d):
        if stamp is None or p.name[:21] >= stamp:
            items.extend((f"traces/{p.name}", n, obj, raw) for n, obj, raw in _lines(p))
    files = list(dict.fromkeys(i[0] for i in items))
    if not any(not i[0].startswith("traces/") for i in items):
        return {"lines": 0, "files": [], "path": None}
    path = Path(path)
    if d.resolve() in (path.resolve(), *path.resolve().parents):
        raise ValueError(f"export FILE must not be inside the dev directory ({d}); pick a path outside it")
    if path.is_dir():
        raise ValueError(f"export FILE {path} is a directory; give a file name")
    path.parent.mkdir(parents=True, exist_ok=True)

    def dump(o) -> str:
        return json.dumps(o, sort_keys=True, ensure_ascii=False, default=str) + "\n"

    body = [dump({"keygraph_dev_export": 1, "keygraph": __version__, "files": files})]
    for name, n, obj, raw in items:
        body.append(dump({"file": name, "line": n, "event": obj}) if obj is not None else dump({"file": name, "line": n, "raw": devlog.mask_text(raw[:2000])}))
    data = "".join(body).encode("utf-8", "replace")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as raw_f:
        if path.name.endswith(".gz"):
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=0) as gz:
                gz.write(data)
        else:
            raw_f.write(data)
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return {"lines": len(items), "files": files, "path": str(path)}
