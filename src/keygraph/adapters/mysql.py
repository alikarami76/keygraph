"""MySQL / MariaDB through information_schema. Works with any run(sql) callable: a DB-API or SQLAlchemy
wrapper, or an external read-only command (see fetch.command_runner). Read-only: SELECTs on information_schema
plus bounded sample SELECTs, all through assert_select."""

from __future__ import annotations

from ..model import Schema
from .base import Source, guarded
from .dump import load_dump
from .fetch import fetch_mysql_all


class MySQLSource(Source):
    kind, dialect, quote = "mysql", "mysql", "`"

    def __init__(self, run, schema_expr: str = "DATABASE()", schema_name: str | None = None, limit: int = 10000):
        self.run, self.schema_expr, self.schema_name, self.limit = guarded(run), schema_expr, schema_name, limit

    def schema(self) -> Schema:
        d = fetch_mysql_all(self.run, self.schema_expr, self.limit)
        if self.schema_name:
            for rows in d.values():
                for r in rows:
                    r["TABLE_SCHEMA"] = self.schema_name
        sch = load_dump(d["columns"], d["statistics"] or None, d["key_columns"] or None, tables=d["tables"])
        for r in d["checks"]:
            t = sch.tables.get(f"{r.get('TABLE_SCHEMA')}.{r.get('TABLE_NAME')}")
            if t:
                t.info.setdefault("checks", []).append({"name": r.get("CONSTRAINT_NAME"), "expr": r.get("CHECK_CLAUSE")})
        return sch
