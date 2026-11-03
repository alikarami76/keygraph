"""Load information_schema-style dumps (JSON, JSONL, CSV) with configurable field mapping."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable, Iterable

from ..model import Column, ForeignKey, Index, Schema

# Canonical field names are the MySQL information_schema ones. A mapping renames YOUR fields to these:
# {"schema": "database", "table": "table", "column": "column", "raw": "raw"} style is expressed as
# {"TABLE_SCHEMA": "database", "TABLE_NAME": "table", "COLUMN_NAME": "column"}: canonical -> source field.
# A field mapped to "a.b" reads the nested key b of key a. "raw" flattening: set "_flatten": ["raw"].


def read_rows(path: str | Path) -> list[dict]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    text = p.read_text(encoding="utf-8")
    if suffix in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("rows", "items", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError(f"{p}: JSON object without a rows/items/data/results list")
    return data


def _get(row: dict, key: str, mapping: dict, default=None):
    src = mapping.get(key, key)
    for cand in (src, src.lower(), src.upper()):
        if "." in cand:
            cur = row
            for part in cand.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is not None:
                return cur
        elif cand in row and row[cand] is not None:
            return row[cand]
    return default


def _flat(row: dict, flatten: Iterable[str]) -> dict:
    out = dict(row)
    for k in flatten:
        if isinstance(out.get(k), dict):
            out.update(out[k])
    return out


def _bool_yes(v) -> bool:
    return str(v).strip().lower() in ("yes", "y", "true", "1", "t")


def load_dump(columns: str | Path | list | None = None, statistics: str | Path | list | None = None,
              key_columns: str | Path | list | None = None, default_schema: str = "main",
              columns_map: dict | None = None, statistics_map: dict | None = None,
              kcu_map: dict | None = None, row_filter: Callable[[dict], bool] | None = None,
              schema_map: Callable[[dict], str] | None = None) -> Schema:
    """Build a Schema. Each argument is a path or an already-loaded list of row dicts.

    Missing STATISTICS: indexes fall back to COLUMN_KEY (PRI/UNI/MUL), marked approximate.
    Missing KEY_COLUMN_USAGE: no declared foreign keys (everything is inferred).
    """
    cm, sm, km = columns_map or {}, statistics_map or {}, kcu_map or {}
    flat_c = cm.get("_flatten", [])
    sch = Schema()

    def rows_of(src, mp):
        if src is None:
            return None
        rows = src if isinstance(src, list) else read_rows(src)
        return [_flat(r, mp.get("_flatten", [])) for r in rows]

    def tschema(row, mp):
        if schema_map:
            return schema_map(row)
        v = _get(row, "TABLE_SCHEMA", mp)
        return str(v) if v not in (None, "") else default_schema

    col_rows = rows_of(columns, cm) or []
    for r in col_rows:
        if row_filter and not row_filter(r):
            continue
        name = _get(r, "TABLE_NAME", cm)
        cname = _get(r, "COLUMN_NAME", cm)
        if not name or not cname:
            continue
        t = sch.get_or_create(tschema(r, cm), str(name))
        ctype = _get(r, "COLUMN_TYPE", cm) or _get(r, "DATA_TYPE", cm) or ""
        t.columns.append(Column(str(cname), str(ctype), not (str(_get(r, "IS_NULLABLE", cm, "YES")).upper() == "NO"),
                                int(_get(r, "ORDINAL_POSITION", cm, len(t.columns) + 1) or 0),
                                str(_get(r, "COLUMN_KEY", cm, "") or "")))
    stat_rows = rows_of(statistics, sm)
    if stat_rows is not None:
        groups: dict[tuple, dict] = {}
        for r in stat_rows:
            key = (tschema(r, sm), str(_get(r, "TABLE_NAME", sm)), str(_get(r, "INDEX_NAME", sm)))
            g = groups.setdefault(key, {"unique": str(_get(r, "NON_UNIQUE", sm, 1)) in ("0", "False", "false"), "cols": {}})
            col = _get(r, "COLUMN_NAME", sm)
            if col is not None:
                g["cols"][int(_get(r, "SEQ_IN_INDEX", sm, len(g["cols"]) + 1))] = str(col)
        for (s, tn, ixn), g in sorted(groups.items()):
            tid = f"{s}.{tn}"
            if tid not in sch.tables:
                continue
            cols = [g["cols"][k] for k in sorted(g["cols"])]
            sch.tables[tid].indexes.append(Index(ixn, cols, g["unique"], ixn.upper() == "PRIMARY"))
    else:
        for t in sch.tables.values():
            cs = sorted(t.columns, key=lambda c: c.position)
            pk = [c.name for c in cs if c.key == "PRI"]
            if pk:
                t.indexes.append(Index("PRIMARY", pk, True, True))
            for c in cs:
                if c.key == "UNI":
                    t.indexes.append(Index(f"(unique:{c.name})", [c.name], True, False, True))
                elif c.key == "MUL":
                    t.indexes.append(Index(f"(index:{c.name})", [c.name], False, False, True))
    kcu = rows_of(key_columns, km)
    if kcu is not None:
        fks: dict[tuple, dict] = {}
        for r in kcu:
            rt = _get(r, "REFERENCED_TABLE_NAME", km)
            tn = _get(r, "TABLE_NAME", km)
            if not rt:
                if stat_rows is None and str(_get(r, "CONSTRAINT_NAME", km, "")).upper() == "PRIMARY" and tn:
                    t = sch.tables.get(f"{tschema(r, km)}.{tn}")
                    if t and not t.primary_key:
                        t.indexes.append(Index("PRIMARY", [str(_get(r, "COLUMN_NAME", km))], True, True))
                continue
            s = tschema(r, km)
            g = fks.setdefault((s, str(tn), str(_get(r, "CONSTRAINT_NAME", km, "")),
                                str(_get(r, "REFERENCED_TABLE_SCHEMA", km) or s), str(rt)), {})
            g[int(_get(r, "ORDINAL_POSITION", km, len(g) + 1))] = (str(_get(r, "COLUMN_NAME", km)),
                                                                   str(_get(r, "REFERENCED_COLUMN_NAME", km)))
        for (s, tn, cn, rs, rt), g in sorted(fks.items()):
            t = sch.tables.get(f"{s}.{tn}")
            if t is not None:
                pairs = [g[k] for k in sorted(g)]
                t.foreign_keys.append(ForeignKey(cn, [p[0] for p in pairs], rs, rt, [p[1] for p in pairs]))
    return sch
