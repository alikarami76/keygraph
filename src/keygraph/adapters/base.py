"""Pluggable source interface.

A source turns one kind of system into a normalised Schema (metadata) and can sample rows for profiling.
Every source is read-only by construction: SQL goes through `guard` (a single SELECT, no write keywords),
HTTP sources only GET metadata and POST native SELECTs, Mongo sources only call find/list operations.
"""

from __future__ import annotations

from typing import Callable

from ..model import Schema
from ..probe import assert_select


def guarded(run: Callable[[str], list]) -> Callable[[str], list]:
    def safe(sql: str) -> list:
        return run(assert_select(sql))
    return safe


def dicts(rows: list, names: list[str]) -> list[dict]:
    """Rows may be dicts (command tools) or tuples (DB-API / SQLAlchemy); return dicts keyed by `names`."""
    out = []
    for r in rows:
        if isinstance(r, dict):
            low = {k.lower(): v for k, v in r.items()}
            if any(n in r or n.lower() in low for n in names):
                out.append({n: r[n] if n in r else low.get(n.lower()) for n in names})
            else:  # a tool keyed rows by the query's own column names (TABLE_SCHEMA, ...): use the column order
                out.append(dict(zip(names, r.values())))
        else:
            out.append(dict(zip(names, r)))
    return out


class Source:
    kind = "generic"
    dialect = "generic"
    quote = '"'
    read_only = "all SQL passes keygraph.probe.assert_select (single SELECT only)"

    def schema(self) -> Schema:  # pragma: no cover - interface
        raise NotImplementedError

    bare = False  # use bare table names (graph schema is a label, not a real schema)

    def table_ref(self, table_id: str) -> str:
        sch, name = table_id.split(".", 1)
        if self.bare:
            sch = ""
        q = self.quote
        return f"{q}{sch}{q}.{q}{name}{q}" if sch not in ("", "main", "default") else f"{q}{name}{q}"

    def sample(self, table_id: str, cols: list[str], n: int, strategy: str = "limit", pk: str | None = None):
        """Return (cols, rows) for a bounded sample. SQL sources override `run`."""
        from ..profile import build_sample_sql
        sql = build_sample_sql(self.dialect, self.table_ref(table_id), cols, n, strategy, pk, self.quote)
        rows = self.run(sql)  # type: ignore[attr-defined]
        return cols, [tuple(r[c] for c in cols) if isinstance(r, dict) else tuple(r) for r in rows]
