"""SQL Server through INFORMATION_SCHEMA and sys.* via a run(sql) callable. Read-only (single SELECTs).
Samples use SELECT TOP n."""

from __future__ import annotations

from ..model import Column, ForeignKey, Index, Schema
from .base import Source, dicts, guarded

COLS = ("SELECT TOP 200000 c.TABLE_SCHEMA AS [schema], c.TABLE_NAME AS [table], c.COLUMN_NAME AS [column], c.ORDINAL_POSITION AS [pos], c.DATA_TYPE + CASE WHEN c.CHARACTER_MAXIMUM_LENGTH IS NOT NULL "
        "THEN '(' + CAST(c.CHARACTER_MAXIMUM_LENGTH AS varchar(10)) + ')' ELSE '' END AS [type], c.IS_NULLABLE AS [nullable], c.COLUMN_DEFAULT AS [default], c.COLLATION_NAME AS [collation], c.CHARACTER_SET_NAME AS [charset] "
        "FROM INFORMATION_SCHEMA.COLUMNS c JOIN INFORMATION_SCHEMA.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME "
        "WHERE t.TABLE_TYPE='BASE TABLE' ORDER BY 1,2,4")
COLN = ["schema", "table", "column", "pos", "type", "nullable", "default", "collation", "charset"]
IDX = ("SELECT TOP 200000 s.name AS [schema], t.name AS [table], i.name AS [index], i.is_unique AS [unique], i.is_primary_key AS [primary], c.name AS [col], ic.key_ordinal AS [ord] FROM sys.indexes i "
       "JOIN sys.tables t ON t.object_id=i.object_id JOIN sys.schemas s ON s.schema_id=t.schema_id "
       "JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id AND ic.is_included_column=0 "
       "JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE i.name IS NOT NULL ORDER BY 1,2,3,7")
IDXN = ["schema", "table", "index", "unique", "primary", "col", "ord"]
FK = ("SELECT TOP 100000 s.name AS [schema], t.name AS [table], fk.name AS [name], c.name AS [col], rs.name AS [ref_schema], rt.name AS [ref_table], rc.name AS [ref_col], fkc.constraint_column_id AS [ord] FROM sys.foreign_key_columns fkc "
      "JOIN sys.foreign_keys fk ON fk.object_id=fkc.constraint_object_id JOIN sys.tables t ON t.object_id=fkc.parent_object_id "
      "JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.columns c ON c.object_id=fkc.parent_object_id AND c.column_id=fkc.parent_column_id "
      "JOIN sys.tables rt ON rt.object_id=fkc.referenced_object_id JOIN sys.schemas rs ON rs.schema_id=rt.schema_id "
      "JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id AND rc.column_id=fkc.referenced_column_id ORDER BY 1,2,3,8")
FKN = ["schema", "table", "name", "col", "ref_schema", "ref_table", "ref_col", "ord"]
ROWS = ("SELECT TOP 200000 s.name AS [schema], t.name AS [table], SUM(p.rows) AS [rows] FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id "
        "JOIN sys.partitions p ON p.object_id=t.object_id AND p.index_id IN (0,1) GROUP BY s.name, t.name")
CHECKS = "SELECT TOP 100000 s.name AS [schema], t.name AS [table], k.name AS [name], k.definition AS [expr] FROM sys.check_constraints k JOIN sys.tables t ON t.object_id=k.parent_object_id JOIN sys.schemas s ON s.schema_id=t.schema_id"


class SQLServerSource(Source):
    kind, dialect, quote = "sqlserver", "sqlserver", '"'

    def __init__(self, run):
        self.run = guarded(run)

    def schema(self) -> Schema:
        sch = Schema()
        for r in dicts(self.run(COLS), COLN):
            t = sch.get_or_create(r["schema"], r["table"])
            t.columns.append(Column(r["column"], str(r["type"]), str(r["nullable"]).upper() != "NO", int(r["pos"]),
                                    default=None if r["default"] is None else str(r["default"]),
                                    charset=r["charset"] or "", collation=r["collation"] or ""))
        groups: dict = {}
        for r in dicts(self.run(IDX), IDXN):
            groups.setdefault((r["schema"], r["table"], r["index"]), {"u": bool(r["unique"]), "p": bool(r["primary"]), "c": []})["c"].append(r["col"])
        for (s, tn, name), g in groups.items():
            if f"{s}.{tn}" in sch.tables:
                sch.tables[f"{s}.{tn}"].indexes.append(Index(name, g["c"], g["u"], g["p"]))
        fks: dict = {}
        for r in dicts(self.run(FK), FKN):
            g = fks.setdefault((r["schema"], r["table"], r["name"], r["ref_schema"], r["ref_table"]), ([], []))
            g[0].append(r["col"])
            g[1].append(r["ref_col"])
        for (s, tn, name, rs, rt), (cols, rcols) in fks.items():
            if f"{s}.{tn}" in sch.tables:
                sch.tables[f"{s}.{tn}"].foreign_keys.append(ForeignKey(name, cols, rs, rt, rcols))
        for r in dicts(self.run(ROWS), ["schema", "table", "rows"]):
            t = sch.tables.get(f"{r['schema']}.{r['table']}")
            if t and r["rows"] is not None:
                t.info["rows_estimate"] = int(r["rows"])
        for r in dicts(self.run(CHECKS), ["schema", "table", "name", "expr"]):
            t = sch.tables.get(f"{r['schema']}.{r['table']}")
            if t:
                t.info.setdefault("checks", []).append({"name": r["name"], "expr": r["expr"]})
        return sch
