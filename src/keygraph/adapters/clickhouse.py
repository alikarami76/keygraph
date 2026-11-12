"""ClickHouse through system.tables / system.columns / system.data_skipping_indices via a run(sql) callable.

"Keys": the primary key columns become the PRIMARY index (ClickHouse primary keys are sorting prefixes, not unique),
the ORDER BY key becomes an index named 'ORDER BY', data-skipping indices become indexes, and the partition key is
kept in table info. Enum8/Enum16 members and LowCardinality/Nullable wrappers are understood. Read-only (SELECT only)."""

from __future__ import annotations

import re

from ..model import Column, Index, Schema, parse_enum_members
from .base import Source, dicts, guarded

TABLES = ("SELECT database, name, engine, total_rows, total_bytes, partition_key, sorting_key, primary_key, comment FROM system.tables "
          "WHERE database IN ({dbs}) AND NOT is_temporary AND engine NOT LIKE '%View' LIMIT 100000")
TABN = ["database", "name", "engine", "total_rows", "total_bytes", "partition_key", "sorting_key", "primary_key", "comment"]
COLUMNS = ("SELECT database, table, name, position, type, default_kind, default_expression, comment FROM system.columns "
           "WHERE database IN ({dbs}) ORDER BY database, table, position LIMIT 500000")
COLN = ["database", "table", "name", "position", "type", "default_kind", "default_expression", "comment"]
SKIP = "SELECT database, table, name, type, expr, granularity FROM system.data_skipping_indices WHERE database IN ({dbs}) LIMIT 100000"
SKIPN = ["database", "table", "name", "type", "expr", "granularity"]


def key_columns(expr: str | None, known: set[str]) -> list[str]:
    """Columns named in a key expression such as 'tuple(a, toDate(b))' or 'a, b' (functions are looked through)."""
    return [t for t in dict.fromkeys(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr or "")) if t in known]


class ClickHouseSource(Source):
    kind, dialect, quote = "clickhouse", "clickhouse", '"'

    def __init__(self, run, databases: list[str] | None = None):
        self.run = guarded(run)
        self.dbs = ", ".join("'" + d.replace("'", "") + "'" for d in databases) if databases else "currentDatabase()"

    def schema(self) -> Schema:
        sch = Schema()
        info = {(r["database"], r["name"]): r for r in dicts(self.fetch("tables", TABLES.format(dbs=self.dbs)), TABN)}
        for r in dicts(self.fetch("columns", COLUMNS.format(dbs=self.dbs)), COLN):
            if (r["database"], r["table"]) not in info:
                continue
            t = sch.get_or_create(r["database"], r["table"])
            ty = str(r["type"])
            t.columns.append(Column(r["name"], ty, ty.startswith("Nullable(") or "Nullable(" in ty, int(r["position"]),
                                    default=r["default_expression"] or None, extra=r["default_kind"] or "", comment=r["comment"] or "",
                                    values=parse_enum_members(ty), generated=r["default_expression"] if r["default_kind"] in ("MATERIALIZED", "ALIAS") else ""))
        for (db, name), r in info.items():
            t = sch.tables.get(f"{db}.{name}")
            if not t:
                continue
            known = {c.name for c in t.columns}
            pk = key_columns(r["primary_key"], known)
            if pk:
                t.indexes.append(Index("PRIMARY", pk, False, True))
            sk = key_columns(r["sorting_key"], known)
            if sk and sk != pk:
                t.indexes.append(Index("ORDER BY", sk))
            t.info.update({k: v for k, v in (("engine", r["engine"]), ("rows_estimate", r["total_rows"]), ("data_bytes", r["total_bytes"]),
                                             ("partition_key", r["partition_key"]), ("sorting_key", r["sorting_key"]), ("comment", r["comment"]))
                           if v not in (None, "")})
        for r in dicts(self.fetch("data-skipping indexes", SKIP.format(dbs=self.dbs)), SKIPN):
            t = sch.tables.get(f"{r['database']}.{r['table']}")
            if t:
                cols = key_columns(r["expr"], {c.name for c in t.columns})
                if cols:
                    t.indexes.append(Index(f"{r['name']} ({r['type']})", cols))
        return sch
