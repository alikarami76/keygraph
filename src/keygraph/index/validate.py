"""Check an agent's answer against the map before anything is stored.

`validate(answer, g, ctx, packet)` runs the structure check from schema.py, then rejects single items with a short
reason the agent can act on (it resubmits). It never echoes a rejected value back: reasons name the field, not the text.
Rules: tables and columns must exist (relations may reach any graph table, but one end must be in the unit), codes must be
well formed, a relation must not repeat or contradict a declared foreign key or touch a confirmed override, a field must not
overwrite a verified note, and no text may look like a personal value or a secret."""

from __future__ import annotations

import json
import re

from ..code.refs import mask_secrets
from . import schema as S
from .pii import holds_secret, looks_pii

CODE_KEY_MAX = 40
_PATH = re.compile(r"^\$\.(\w+)\[(\d+)\](?:\.(.*?))?: (.*)$")
# fields of a verified note that an answer must not change: item field -> (table, stored column)
_NOTE_FIELDS = {"grain": "grain", "soft_delete": "soft_delete", "freshness": "freshness", "purpose": "summary"}
_COL_FIELDS = {"meaning": "meaning", "unit": "unit", "null_meaning": "null_meaning"}


def _strings(v, path: str = ""):
    """Every (field path, text) in an item, including list entries and the keys and values of `codes`."""
    if isinstance(v, str):
        yield path, v
    elif isinstance(v, dict):
        for k, x in v.items():
            sub = f"{path}.{k}" if path else str(k)
            if path:
                yield sub, str(k)
            yield from _strings(x, sub)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _strings(x, f"{path}[{i}]")


def _text_problem(path: str, text: str) -> str | None:
    field = re.split(r"[.\[]", path)[0]
    kind = looks_pii(text)
    if kind:
        return f"{field}: looks like a personal value ({kind}); remove it and describe the column in words"
    if text and (holds_secret(text) or mask_secrets(text) != text):
        return f"{field}: looks like it holds a secret or credential; reword it without the value"
    return None


def _cols(g, tid: str) -> set[str]:
    return {c["name"] for c in g.nodes[tid]["columns"]}


def _label(kind: str, i: int) -> str:
    return f"{kind}[{i}]"


def _table_problem(g, unit: set[str], t, role: str = "table", must_be_unit: bool = True) -> str | None:
    if not isinstance(t, str) or t not in g.nodes:
        return f"{role} is not a table in the map; write it as schema.table exactly as the packet lists it"
    if must_be_unit and t not in unit:
        return f"{role} {t} is outside this unit; use only the unit tables"
    return None


def _columns_problem(g, tid: str, cols, role: str) -> str | None:
    have = _cols(g, tid)
    bad = [c for c in cols if c not in have]
    if bad:
        return f"unknown column {bad[0]} on {tid} ({role}); use the column names the packet lists"
    return None


def _codes_problem(codes) -> str | None:
    if not isinstance(codes, dict):
        return "codes must be an object of code to meaning"
    seen: set[str] = set()
    for k, v in codes.items():
        if not str(k).strip() or len(str(k)) > CODE_KEY_MAX or re.search(r"[\x00-\x1f]", str(k)):
            return f"codes: a code must be 1 to {CODE_KEY_MAX} visible characters"
        if not isinstance(v, str) or not v.strip():
            return "codes: every code needs a short meaning text"
        if str(k).strip() in seen:
            return "codes: the same code is listed twice"
        seen.add(str(k).strip())
    return None


def _declared(g) -> list[dict]:
    return [e for e in g.edges if e.get("kind") == "declared"]


def _relation_problem(g, unit: set[str], r: dict) -> str | None:
    ft, tt = r["from_table"], r["to_table"]
    for role, t in (("from_table", ft), ("to_table", tt)):
        p = _table_problem(g, unit, t, role, must_be_unit=False)
        if p:
            return p
    if ft not in unit and tt not in unit:
        return "relation touches no table of this unit; one end must be a unit table"
    if len(r["from_columns"]) != len(r["to_columns"]) or not r["from_columns"]:
        return "from_columns and to_columns must list the same number of columns (at least one)"
    p = _columns_problem(g, ft, r["from_columns"], "from_columns") or _columns_problem(g, tt, r["to_columns"], "to_columns")
    if p:
        return p
    fc, tc = list(r["from_columns"]), list(r["to_columns"])
    for e in _declared(g):
        same = (e["from"], e["from_columns"], e["to"], e["to_columns"]) == (ft, fc, tt, tc) or \
               (e["to"], e["to_columns"], e["from"], e["from_columns"]) == (ft, fc, tt, tc)
        if same:
            return f"already declared as foreign key {e.get('constraint') or e['id']}; leave declared relations out"
        a, b = (e["from"], set(e["from_columns"])), (e["to"], set(e["to_columns"]))
        mine_f, mine_t = (ft, set(fc)), (tt, set(tc))
        if (mine_f == a and mine_t != b) or (mine_t == a and mine_f != b):  # also when the claim is written the other way round
            return (f"contradicts declared foreign key {e.get('constraint') or e['id']}, which points {e['from']}({', '.join(e['from_columns'])}) at "
                    f"{e['to']}({', '.join(e['to_columns'])}); declared keys always win")
    for e in g.edges:
        if e.get("status") in ("confirmed", "added") and {(e["from"], tuple(e["from_columns"])), (e["to"], tuple(e["to_columns"]))} == \
                {(ft, tuple(fc)), (tt, tuple(tc))}:
            return "this relation is already settled by a user override; leave it out"
    return None


def _row(ctx, sql: str, *args):
    if ctx is None:
        return None
    try:
        return ctx.db.execute(sql, args).fetchone()
    except Exception:  # noqa: BLE001  (a context file without these tables)
        return None


def _same(a, b) -> bool:
    return " ".join(str(a).lower().split()) == " ".join(str(b).lower().split())


def _verified_note_problem(ctx, kind: str, it: dict) -> str | None:
    if kind == "tables":
        row = _row(ctx, "SELECT * FROM table_notes WHERE table_id=? AND verified=1", it["table"])
        if row:
            for f, col in _NOTE_FIELDS.items():
                if it.get(f) and row[col] and not _same(it[f], row[col]):
                    return f"{f}: {it['table']} has a verified note; leave {f} out"
    elif kind == "columns":
        row = _row(ctx, "SELECT * FROM column_semantics WHERE table_id=? AND col=? AND verified=1", it["table"], it["column"])
        if row:
            for f, col in _COL_FIELDS.items():
                if it.get(f) and row[col] and not _same(it[f], row[col]):
                    return f"{f}: {it['table']}.{it['column']} has a verified meaning; leave {f} out"
            try:
                had = json.loads(row["codes"] or "{}")
            except ValueError:
                had = {}
            if it.get("codes") and had and {str(k): str(v).lower() for k, v in it["codes"].items()} != \
                    {str(k): str(v).lower() for k, v in had.items()}:
                return f"codes: {it['table']}.{it['column']} has verified codes; leave codes out"
    return None


def _item_problem(g, ctx, unit: set[str], kind: str, it: dict) -> str | None:
    if kind == "relations":
        p = _relation_problem(g, unit, it)
    elif kind in ("columns", "tables", "performance"):
        p = _table_problem(g, unit, it["table"])
        if not p and kind == "columns":
            p = _columns_problem(g, it["table"], [it["column"]], "column") or (_codes_problem(it["codes"]) if "codes" in it else None)
        if not p and kind == "tables" and it.get("grain_columns"):
            p = _columns_problem(g, it["table"], it["grain_columns"], "grain_columns")
    else:  # concepts, metrics: tables may be any graph table
        p = next((q for q in (_table_problem(g, unit, t, "tables entry", must_be_unit=False) for t in it["tables"]) if q), None)
    if p:
        return p
    for path, text in _strings(it):
        q = _text_problem(path, text)
        if q:
            return q
    return _verified_note_problem(ctx, kind, it)


def validate(answer, g, ctx, packet: dict, tasks=None, require_all: bool = True) -> dict:
    """{"accepted": [{kind, index, item}], "rejected": [{kind, index, item, reason}], "errors": [...], "counts": {...}}.

    `errors` holds whole-answer structure problems (then nothing is accepted). `item` in a rejection is a label such as
    `relations[2]`, never the text. `tasks` defaults to the kinds present in the answer. A missing kind is one error naming
    them all; with require_all=False (a resubmit of rejected items) a missing kind just means no items of it."""
    out: dict = {"accepted": [], "rejected": [], "errors": []}
    if not isinstance(answer, dict):
        out["errors"] = ["$: the answer must be one JSON object"]
        return out
    kinds = [k for k in S.TASKS if k in answer] if tasks is None else S.kinds_of(tasks)
    if not kinds:
        out["errors"] = ["$: the answer holds no task lists (relations, columns, tables, concepts, metrics, performance)"]
        return out
    missing = [k for k in kinds if k not in answer]
    if missing and not require_all and len(missing) == len(kinds):
        out["errors"] = ["$: the answer holds no items; send the rejected items again under their kind"]
        return out
    if missing and require_all:
        names = ", ".join(f"`{k}`" for k in missing)
        out["errors"].append(f"$: missing {names}: include {'it' if len(missing) == 1 else 'each'} as [] if you have nothing to add")
    answer = {**answer, **{k: [] for k in missing}}
    per_item: dict[tuple[str, int], list[str]] = {}
    for e in S.validate_answer(answer, kinds):
        m = _PATH.match(e)
        if m:
            per_item.setdefault((m.group(1), int(m.group(2))), []).append(f"{m.group(3) or 'item'}: {m.group(4)}")
        else:
            out["errors"].append(e)
    if out["errors"]:
        return out
    unit = set(packet.get("unit_tables") or [])
    for kind in kinds:
        for i, it in enumerate(answer[kind]):
            reason = "; ".join(per_item[(kind, i)]) if (kind, i) in per_item else _item_problem(g, ctx, unit, kind, it)
            if reason:
                out["rejected"].append({"kind": kind, "index": i, "item": _label(kind, i), "reason": reason})
            else:
                out["accepted"].append({"kind": kind, "index": i, "item": it})
    return out
