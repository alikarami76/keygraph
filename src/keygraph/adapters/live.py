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
