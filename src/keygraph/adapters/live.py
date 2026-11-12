"""Live scan through the SQLAlchemy inspector. Metadata only; never reads table rows."""

from __future__ import annotations

from ..model import Column, ForeignKey, Index, Schema, Table

SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys", "pg_catalog", "pg_toast"}


def scan_url(url: str, schemas: list[str] | None = None, all_schemas: bool = False) -> Schema:
    try:
        from sqlalchemy import create_engine, inspect
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("live scanning needs SQLAlchemy: pip install 'keygraph[live]'") from exc
    engine = create_engine(url)
    insp = inspect(engine)
    default = insp.default_schema_name or "main"
    if all_schemas:
        names = [s for s in insp.get_schema_names() if s.lower() not in SYSTEM_SCHEMAS and not s.startswith("pg_")]
    else:
        names = schemas or [default]
    sch = Schema()
    for s in names:
        qual = None if s == default else s
        for tn in insp.get_table_names(schema=qual):
            t = Table(s, tn)
            pk = insp.get_pk_constraint(tn, schema=qual) or {}
            pkcols = list(pk.get("constrained_columns") or [])
            for i, c in enumerate(insp.get_columns(tn, schema=qual), 1):
                t.columns.append(Column(c["name"], str(c["type"]), bool(c.get("nullable", True)), i,
                                        "PRI" if c["name"] in pkcols else ""))
            if pkcols:
                t.indexes.append(Index("PRIMARY", pkcols, True, True))
            seen = {tuple(pkcols)} if pkcols else set()
            for u in insp.get_unique_constraints(tn, schema=qual):
                cols = [c for c in u.get("column_names", []) if c]
                if cols and tuple(cols) not in seen:
                    seen.add(tuple(cols))
                    t.indexes.append(Index(u.get("name") or f"uq_{'_'.join(cols)}", cols, True))
            for ix in insp.get_indexes(tn, schema=qual):
                cols = [c for c in ix.get("column_names", []) if c]
                if not cols or (ix.get("unique") and tuple(cols) in seen):
                    continue
                t.indexes.append(Index(ix.get("name") or f"ix_{'_'.join(cols)}", cols, bool(ix.get("unique"))))
            for fk in insp.get_foreign_keys(tn, schema=qual):
                t.foreign_keys.append(ForeignKey(fk.get("name") or "", list(fk["constrained_columns"]),
                                                 fk.get("referred_schema") or s, fk["referred_table"],
                                                 list(fk["referred_columns"])))
            sch.add(t)
    return sch


DEDICATED = {"mysql": "mysql", "mariadb": "mysql", "postgresql": "postgres", "postgres": "postgres", "mssql": "sqlserver",
             "clickhouse": "clickhouse", "clickhousedb": "clickhouse"}
DEFAULT_SCHEMA = {"postgres": "public", "sqlserver": "dbo"}


def url_dialect(url: str) -> str:
    return url.split(":", 1)[0].split("+", 1)[0].lower()


def _lit(name: str) -> str:
    if not name or any(ch in name for ch in "'\\\x00"):
        raise ValueError("schema name cannot be quoted safely")
    return "'" + name + "'"


def _keep(sch: Schema, names: set[str]) -> Schema:
    """Only the given schemas (case-insensitive); foreign keys leaving them are dropped."""
    low = {n.lower() for n in names}
    out = Schema()
    for t in sch.tables.values():
        if t.schema.lower() in low:
            out.add(t)
    for t in out.tables.values():
        t.foreign_keys = [f for f in t.foreign_keys if f"{f.ref_schema}.{f.ref_table}" in out.tables]
    return out


def dedicated_scan(kind: str, url: str, schemas: list[str] | None, all_schemas: bool) -> Schema:
    """Scan with the engine's own adapter (comments, enums, row counts, partitions), SELECT-only through assert_select.
    Honours --schema / --all-schemas the way the generic inspector scan does."""
    from . import open_source
    from ..probe import SelectOnlyRunner
    run = SelectOnlyRunner.from_url(url, as_dicts=True).run

    def names_of(sql: str) -> list[str]:
        return [str(next(iter(r.values())) if isinstance(r, dict) else r[0]) for r in run(sql)]
    ok = lambda n: n.lower() not in SYSTEM_SCHEMAS and n.lower() != "system" and not n.startswith("pg_")  # noqa: E731
    if kind == "mysql":
        if not schemas and not all_schemas:
            return open_source("mysql", run=run).schema()
        names = schemas or [n for n in names_of("SELECT schema_name FROM information_schema.SCHEMATA") if ok(n)]
        out = Schema()
        for n in names:
            out.tables.update(open_source("mysql", run=run, schema_expr=_lit(n)).schema().tables)
        return out
    if kind == "clickhouse":
        names = schemas or ([n for n in names_of("SELECT name FROM system.databases") if ok(n) and n.lower() != "information_schema"]
                            if all_schemas else None)
        return open_source("clickhouse", run=run, databases=names).schema()
    sch = open_source(kind, run=run).schema()
    if all_schemas:
        return _keep(sch, {t.schema for t in sch.tables.values() if ok(t.schema)})
    return _keep(sch, set(schemas or [DEFAULT_SCHEMA[kind]]))


def scan_any(url: str, schemas: list[str] | None = None, all_schemas: bool = False, generic: bool = False) -> tuple[Schema, str]:
    """Scan a database URL; returns (schema, kind of path used). Dedicated adapter first, inspector otherwise or on failure."""
    import sys
    kind = DEDICATED.get(url_dialect(url))
    if kind and not generic:
        try:
            return dedicated_scan(kind, url, schemas, all_schemas), kind
        except Exception as exc:  # noqa: BLE001
            print(f"keygraph: the {kind} adapter failed ({type(exc).__name__}); falling back to the generic SQLAlchemy scan", file=sys.stderr)
    return scan_url(url, schemas, all_schemas), "sqlalchemy"
