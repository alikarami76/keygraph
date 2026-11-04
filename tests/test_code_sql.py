"""SQL in code: literal extraction, placeholders, sqlglot resolution and the token fallback."""
import json

import pytest

from keygraph.code import sql as SQL
from keygraph.code.vocab import Vocab
from tests.codekit import GOLDEN, build_graph_dir, sql_snapshot

V = Vocab({"app.orders": ["id", "customer_id", "status", "total"], "app.customers": ["id", "email", "status"],
           "app.payments": ["id", "order_id", "amount"]})


@pytest.fixture(scope="module")
def vocab(tmp_path_factory):
    return Vocab.load(build_graph_dir(tmp_path_factory.mktemp("code_sql")))


@pytest.fixture()
def no_sqlglot(monkeypatch):
    monkeypatch.setattr(SQL, "sqlglot", None)


def test_raw_sql_repo_matches_golden(vocab):
    mode = "tokens" if SQL.sqlglot is None else "sqlglot"
    want = json.loads((GOLDEN / f"raw-sql.{mode}.json").read_text(encoding="utf-8"))
    assert sql_snapshot("raw-sql", vocab) == want


def test_raw_sql_repo_without_sqlglot_matches_token_golden(vocab, no_sqlglot):
    want = json.loads((GOLDEN / "raw-sql.tokens.json").read_text(encoding="utf-8"))
    assert sql_snapshot("raw-sql", vocab) == want


def test_string_literals_per_language_skip_comments():
    py = 'q = """\nSELECT 1\n"""  # don\'t\nx = f"SELECT {a} FROM t"\n'
    assert [s for _, s in SQL.strings(py, "py")] == ["\nSELECT 1\n", "SELECT {a} FROM t"]
    php = "<?php\n// it's here\n$a = <<<SQL\n  SELECT id FROM orders\n  SQL;\n#[Attr('x')]\n$b = \"UPDATE t\";\n"
    assert [s for _, s in SQL.strings(php, "php")] == ["  SELECT id FROM orders", "x", "UPDATE t"]
    ts = "/* it's */ const q = `SELECT * FROM ${t}`;\n"
    off, body = SQL.strings(ts, "ts")[0]
    assert body == "SELECT * FROM ${t}" and ts[off:off + 6] == "SELECT"


def test_sql_candidates_and_placeholders():
    assert SQL.is_sql("  select id from x") and SQL.is_sql("count(*) FROM orders")
    assert not SQL.is_sql("copied from clipboard") and not SQL.is_sql("Selected items")
    assert SQL.neutralize("a = ? AND b = :name AND c = %s AND d = {$x} AND e = ${y} AND f = $z AND g = %(n)s AND h::int") \
        == "a = 0 AND b = 0 AND c = 0 AND d = 0 AND e = 0 AND f = 0 AND g = 0 AND h::int"
    stmts = SQL.statements("-- head\nSELECT 1 FROM a;\n\nDELETE FROM b;\n", "sql")
    assert [s for _, s in stmts] == ["SELECT 1 FROM a", "DELETE FROM b"]


def test_token_fallback_reads_and_writes(no_sqlglot):
    hits = SQL.analyze("INSERT INTO orders (id) SELECT id FROM customers JOIN payments p ON 1=1", V)
    assert [(h.kind, h.table, h.via) for h in hits] == [("sql_write", "orders", "tokens"), ("sql_read", "customers", "tokens"),
                                                        ("sql_read", "payments", "tokens")]
    assert [(h.kind, h.table) for h in SQL.analyze("DELETE FROM orders WHERE id IN (SELECT order_id FROM payments)", V)] \
        == [("sql_write", "orders"), ("sql_read", "payments")]


def test_sqlglot_aliases_writes_and_unqualified_columns():
    pytest.importorskip("sqlglot")
    hits = SQL.analyze("SELECT o.total, email FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = 1", V)
    got = {(h.kind, h.table, h.column, h.role, h.confidence) for h in hits}
    assert ("sql_read", "orders", "total", "read", 0.95) in got
    assert ("sql_read", "customers", "email", "read", 0.85) in got  # unqualified, only customers has it
    assert ("sql_read", "orders", "customer_id", "join", 0.95) in got
    assert ("sql_read", "orders", "status", "filter", 0.95) in got
    up = {(h.kind, h.table, h.column) for h in SQL.analyze("UPDATE orders SET status = 2 WHERE id = 7", V)}
    assert up == {("sql_write", "orders", None), ("sql_write", "orders", "status"), ("sql_read", "orders", "id")}
    ins = {(h.kind, h.table, h.column) for h in SQL.analyze("INSERT INTO payments (order_id, amount) VALUES (?, ?)", V)}
    assert ins == {("sql_write", "payments", None), ("sql_write", "payments", "order_id"), ("sql_write", "payments", "amount")}
    cte = {h.table for h in SQL.analyze("WITH x AS (SELECT id FROM orders) SELECT * FROM x", V)}
    assert cte == {"orders"}


def test_unparseable_statement_falls_back_to_tokens():
    pytest.importorskip("sqlglot")
    hits = SQL.analyze("REPLACE INTO orders (id) VALUES (1)", V)
    assert [(h.kind, h.table, h.via) for h in hits] == [("sql_write", "orders", "tokens")]
