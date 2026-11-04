"""PostgreSQL through information_schema and pg_catalog via a run(sql) callable. Read-only (single SELECTs)."""

from __future__ import annotations

from ..model import Column, ForeignKey, Index, Schema
from .base import Source, dicts, guarded

SYS = "('pg_catalog','information_schema','pg_toast')"
COLS = ("SELECT c.table_schema, c.table_name, c.column_name, c.ordinal_position, CASE WHEN c.data_type='USER-DEFINED' THEN c.udt_name "
        "WHEN c.data_type='character varying' THEN 'varchar('||COALESCE(c.character_maximum_length::text,'')||')' ELSE c.data_type END, "
        "c.is_nullable, c.column_default, c.is_generated, c.generation_expression, "
        "pg_catalog.col_description((quote_ident(c.table_schema)||'.'||quote_ident(c.table_name))::regclass::oid, c.ordinal_position) "
        "FROM information_schema.columns c JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name "
        f"WHERE t.table_type='BASE TABLE' AND c.table_schema NOT IN {SYS} ORDER BY 1,2,4 LIMIT 200000")
COLN = ["schema", "table", "column", "pos", "type", "nullable", "default", "generated", "genexpr", "comment"]
IDX = ("SELECT n.nspname, c.relname, i.relname, ix.indisunique, ix.indisprimary, array_to_string(ARRAY(SELECT a.attname FROM "
       "unnest(ix.indkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum ORDER BY k.ord),',') "
       "FROM pg_index ix JOIN pg_class c ON c.oid=ix.indrelid JOIN pg_class i ON i.oid=ix.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
       f"WHERE n.nspname NOT IN {SYS} LIMIT 200000")
IDXN = ["schema", "table", "index", "unique", "primary", "cols"]
FK = ("SELECT n.nspname, c.relname, con.conname, array_to_string(ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum,ord) "
      "JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum ORDER BY k.ord),','), rn.nspname, rc.relname, "
      "array_to_string(ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum,ord) JOIN pg_attribute a ON a.attrelid=con.confrelid "
      "AND a.attnum=k.attnum ORDER BY k.ord),',') FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
      f"JOIN pg_class rc ON rc.oid=con.confrelid JOIN pg_namespace rn ON rn.oid=rc.relnamespace WHERE con.contype='f' AND n.nspname NOT IN {SYS} LIMIT 100000")
FKN = ["schema", "table", "name", "cols", "ref_schema", "ref_table", "ref_cols"]
ENUMS = "SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid GROUP BY t.typname LIMIT 10000"
ROWS = ("SELECT n.nspname, c.relname, c.reltuples::bigint, pg_total_relation_size(c.oid), obj_description(c.oid) FROM pg_class c "
        f"JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN {SYS} LIMIT 200000")
CHECKS = ("SELECT n.nspname, c.relname, con.conname, pg_get_constraintdef(con.oid) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
          f"JOIN pg_namespace n ON n.oid=c.relnamespace WHERE con.contype='c' AND n.nspname NOT IN {SYS} LIMIT 100000")


class PostgresSource(Source):
    kind, dialect, quote = "postgres", "postgres", '"'

    def __init__(self, run):
        self.run = guarded(run)

    def schema(self) -> Schema:
        sch = Schema()
        for r in dicts(self.run(COLS), COLN):
            t = sch.get_or_create(r["schema"], r["table"])
            gen = r["genexpr"] if str(r["generated"]).upper() == "ALWAYS" else ""
            t.columns.append(Column(r["column"], str(r["type"]), str(r["nullable"]).upper() != "NO", int(r["pos"]),
                                    default=None if r["default"] is None else str(r["default"]), comment=r["comment"] or "",
                                    generated=gen or ""))
        enums = {n: v.split(",") for n, v in (tuple(x.values()) if isinstance(x, dict) else x for x in self.run(ENUMS))}
        for t in sch.tables.values():
            for c in t.columns:
                c.values = enums.get(c.type, [])
        for r in dicts(self.run(IDX), IDXN):
            t = sch.tables.get(f"{r['schema']}.{r['table']}")
            if t:
                t.indexes.append(Index(r["index"], r["cols"].split(","), bool(r["unique"]), bool(r["primary"])))
        for r in dicts(self.run(FK), FKN):
            t = sch.tables.get(f"{r['schema']}.{r['table']}")
            if t:
                t.foreign_keys.append(ForeignKey(r["name"], r["cols"].split(","), r["ref_schema"], r["ref_table"], r["ref_cols"].split(",")))
        for r in dicts(self.run(ROWS), ["schema", "table", "rows", "bytes", "comment"]):
            t = sch.tables.get(f"{r['schema']}.{r['table']}")
            if t:
                t.info.update({k: v for k, v in (("rows_estimate", r["rows"]), ("data_bytes", r["bytes"]), ("comment", r["comment"])) if v not in (None, "")})
        for r in dicts(self.run(CHECKS), ["schema", "table", "name", "expr"]):
            t = sch.tables.get(f"{r['schema']}.{r['table']}")
            if t:
                t.info.setdefault("checks", []).append({"name": r["name"], "expr": r["expr"]})
        return sch
