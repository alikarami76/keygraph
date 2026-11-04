"""Shared helpers for the code-index tests: a small built graph and the fixture repos."""

from __future__ import annotations

from pathlib import Path

from keygraph.cli import run_pipeline
from keygraph.model import Column, Index, Schema, Table

CODE_FIX = Path(__file__).parent / "fixtures" / "code"

TABLES = {
    "customers": ["id", "name", "email", "status", "created_at"],
    "orders": ["id", "customer_id", "status", "total", "placed_at", "created_at"],
    "order_items": ["id", "order_id", "product_id", "qty", "unit_price"],
    "products": ["id", "title", "price", "sku"],
    "shipments": ["id", "order_id", "carrier", "tracking_no", "status"],
    "payments": ["id", "order_id", "amount", "status", "gateway_ref"],
    "bug_reports": ["id", "title", "platform", "steps_to_recreate"],
}


def shop_schema() -> Schema:
    s = Schema()
    for name, cols in TABLES.items():
        s.add(Table("app", name, [Column(c, "int" if c == "id" or c.endswith("_id") else "varchar(80)", position=i)
                                  for i, c in enumerate(cols, 1)], [Index("PRIMARY", ["id"], True, True)]))
    return s


def build_graph_dir(out: Path) -> Path:
    """Build the small graph into out/ and return the path of its keygraph.db."""
    run_pipeline(shop_schema(), out, verbose=False)
    return out / "keygraph.db"


GOLDEN = CODE_FIX / "golden"
RULE_REPOS = ["custom-pattern", "go-gorm", "java-jpa", "php-doctrine", "prisma", "python-django", "python-sqlalchemy",
              "ts-typeorm"]


def snapshot(refs, unresolved) -> dict:
    """Golden form: refs without snippets, unresolved names; both already sorted by the extractor."""
    return {"refs": [[r.path, r.line, r.kind, r.table_id, r.column, r.symbol, r.rule, r.confidence] for r in refs],
            "unresolved": [[u.path, u.line, u.kind, u.name, u.rule] for u in unresolved]}


def dumps_golden(obj: dict) -> str:
    """One ref per line, so a golden diff shows exactly which ref changed."""
    import json
    out = ["{"]
    for i, key in enumerate(("refs", "unresolved")):
        rows = obj[key]
        out.append(f' "{key}": [' + ("" if rows else "]" + ("," if i == 0 else "")))
        for j, r in enumerate(rows):
            out.append("  " + json.dumps(r) + ("," if j < len(rows) - 1 else ""))
        if rows:
            out.append(" ]" + ("," if i == 0 else ""))
    out.append("}")
    return "\n".join(out) + "\n"


def rules_snapshot(repo: str, vocab) -> dict:
    from keygraph.code.patterns import builtin_rules, load_patterns, run_rules
    from keygraph.code.walk import read, walk
    root = CODE_FIX / repo
    rules = builtin_rules() + load_patterns(sorted(root.glob("*.yaml")))
    refs, unres = [], []
    for f in walk(root):
        got = read(f)
        res = run_rules(repo, f.path, f.lang, got[1], rules, vocab)
        refs += res.refs
        unres += res.unresolved
    return snapshot(refs, unres)


def sql_snapshot(repo: str, vocab) -> dict:
    from keygraph.code.sql import sql_refs
    from keygraph.code.walk import read, walk
    refs, unres = [], []
    for f in walk(CODE_FIX / repo):
        got = read(f)
        r, u, _ = sql_refs(repo, f.path, f.lang, got[1], vocab)
        refs += r
        unres += u
    return snapshot(refs, unres)
