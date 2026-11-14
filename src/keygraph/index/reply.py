"""Size limit for index replies (CLI --json and MCP): compact JSON and, when still too long, deterministic trimming.

Why: Claude Code saves an MCP tool result over 50,000 characters to a file and shows the model a 2 KB preview, so an agent
without a file tool never sees the packet. Every `index` reply therefore stays under a hard cap of characters (default
40,000, env KEYGRAPH_INDEX_REPLY_CAP to change it), measured on the compact JSON that both surfaces print.

Trimming: only `index next` replies carry enough to need it. The packet loses content in the same priority order as the token
cap (queries, code refs, profiles, neighbours, column details, candidate relations, context), then indexes, then trailing
columns, never the table list, the instructions or the answer schema. The reply says what was cut in `truncated`:
{"cap", "original_chars", "cut": ["packet.queries (items removed): 3", ...], "over_cap": true (only when it still did not fit)}.
Any other reply that is too long loses items from the end of its longest list (`cut` says `rejected (items removed): 12`). The token budget accounting is not
affected: tokens are estimated on the full packet before this runs."""

from __future__ import annotations

import copy
import functools
import json
import os

from . import packet as PK

ENV = "KEYGRAPH_INDEX_REPLY_CAP"
DEFAULT_CAP = 40_000
MIN_CAP = 4_000
PROTECTED = ("tasks",)  # lists the agent needs whole: the kinds of answer it must give


def dumps(obj) -> str:
    """The one JSON form of an index reply: compact separators, unicode kept."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def cap() -> int:
    """The character cap: DEFAULT_CAP unless the environment gives an integer (raised to MIN_CAP when smaller)."""
    try:
        return max(MIN_CAP, int(os.environ.get(ENV, "")))
    except ValueError:
        return DEFAULT_CAP


def _size(r: dict) -> int:
    return len(dumps(r))


def _note(r: dict, limit: int, original: int, counts: dict[str, int]) -> None:
    r["truncated"] = {"cap": limit, "original_chars": original, "cut": [f"{k}: {v}" for k, v in counts.items()]}


def _bump(counts: dict[str, int], key: str, unit: str) -> None:
    label = f"{key} ({unit})"
    counts[label] = counts.get(label, 0) + 1


def _trim_packet(r: dict, limit: int, original: int, counts: dict[str, int]) -> None:
    p = r["packet"]

    def fits() -> bool:
        _note(r, limit, original, counts)
        return _size(r) <= limit

    for sec in PK.TRIM_ORDER:
        if sec == "column_details":
            for t in p["tables"]:
                for c in t["columns"]:
                    for k in ("comment", "values"):
                        if k in c:
                            del c[k]
                            _bump(counts, "packet.column_details", "fields removed")
            if fits():
                return
            continue
        while p.get(sec):
            PK._pop_one(p, sec)
            _bump(counts, f"packet.{sec}", "items removed")
            if fits():
                return
    while p["tables"]:
        t = max(p["tables"], key=lambda t: (len(t["indexes"]), t["table"]))
        if not t["indexes"]:
            break
        t["indexes"].pop()
        _bump(counts, "packet.indexes", "items removed")
        if fits():
            return
    while True:
        widest = max(p["tables"], key=lambda t: (len(t["columns"]), t["table"]))
        keep = set(widest["primary_key"]) | {c for u in widest["unique_keys"] for c in u} | {c for i in widest["indexes"] for c in i["columns"]}
        idx = [i for i, c in enumerate(widest["columns"]) if not c.get("key") and c["name"] not in keep]
        if not idx:
            return
        widest["columns"].pop(idx[-1])
        widest["columns_omitted"] = widest.get("columns_omitted", 0) + 1
        _bump(counts, "packet.columns", "removed")
        if fits():
            return


def _trim_lists(r: dict, limit: int, original: int, counts: dict[str, int]) -> None:
    """Generic fallback: drop items from the end of the longest top-level list until the reply fits."""
    while True:
        _note(r, limit, original, counts)
        if _size(r) <= limit:
            return
        lists = {k: v for k, v in r.items() if isinstance(v, list) and v and k not in PROTECTED}
        if not lists:
            return
        k = max(lists, key=lambda x: (len(lists[x]), x))
        lists[k].pop()
        _bump(counts, k, "items removed")


def fit(reply: dict, limit: int | None = None) -> dict:
    """The reply itself when its compact JSON fits, else a trimmed copy with a `truncated` note (see the module doc)."""
    limit = limit or cap()
    original = _size(reply)
    if original <= limit:
        return reply
    r = copy.deepcopy(reply)
    counts: dict[str, int] = {}
    if isinstance(r.get("packet"), dict) and isinstance(r["packet"].get("tables"), list):
        _trim_packet(r, limit, original, counts)
    if _size(r) > limit and "packet" not in r:  # a unit reply keeps its tasks, instructions and answer schema whole
        _trim_lists(r, limit, original, counts)
    _note(r, limit, original, counts)
    if _size(r) > limit:
        r["truncated"]["over_cap"] = True
    return r


def capped(fn):
    """Decorator for the functions behind `keygraph index ...` and the MCP index tools: their reply passes through fit()."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        return fit(fn(*a, **kw))
    return wrapper
