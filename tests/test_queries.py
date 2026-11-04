"""Query log store and Python API: runs, people, context, reads, JSONL import and the search index."""
import json
import sqlite3

import pytest

from keygraph import queries as QL
from keygraph.code import sql as SQL
from tests.codekit import build_graph_dir


@pytest.fixture()
def db(tmp_path):
    return build_graph_dir(tmp_path)


def test_store_runs_people_context_and_reads(db):
    QL.add(db, "SELECT id FROM orders WHERE status = 1", "mysql", ms=10, by="ana", context="paid orders", at="2026-11-01T10:00:00")
    QL.add(db, "SELECT id FROM orders WHERE status = 2", "mysql", ms=30, by="bo", context="", at="2026-11-02T10:00:00")
    r = QL.add(db, "SELECT id FROM orders WHERE status = 3", "mysql", ms=20, by="ana", context="open orders", at="2026-11-03T10:00:00",
               tags="a, b")
    QL.add(db, "SELECT email FROM customers", "mysql", ms=500, at="2026-11-03T11:00:00")
    top = QL.top(db)["queries"]
    assert [q["runs"] for q in top] == [3, 1]
    q = top[0]
    assert (q["fp"], q["people"], q["context"], q["median_ms"], q["tables"]) == (r["fp"], 2, "open orders", 20, ["app.orders"])
    assert QL.top(db, by="recent")["queries"][0]["tables"] == ["app.customers"]
    assert [x["median_ms"] for x in QL.slow(db)["queries"]] == [500, 20]
    assert QL.slow(db, min_runs=2)["queries"][0]["fp"] == r["fp"]
    assert [x["fp"] for x in QL.for_ref(db, "orders")["queries"]] == [r["fp"]]
    if SQL.sqlglot is not None:  # column links need sqlglot
        assert QL.for_ref(db, "orders.status")["queries"][0]["roles"] == ["filter"]
    assert QL.search(db, "open")["queries"][0]["fp"] == r["fp"]
    shown = QL.show(db, r["fp"][:6])
    assert [x["by"] for x in shown["runs_list"]] == ["ana", "bo", "ana"] and shown["runs_list"][2]["tags"] == ["a", "b"]
    s = QL.stats(db)
    assert (s["runs"], s["fingerprints"], s["failed_runs"], s["sources"]) == (4, 2, 0, {"mysql": 4})
    with pytest.raises(ValueError, match="needs a source"):
        QL.add(db, "SELECT 1", "")


def test_import_jsonl_reports_bad_lines_and_unresolved(db, tmp_path):
    f = tmp_path / "q.jsonl"
    f.write_text("\n".join([
        json.dumps({"sql": "SELECT id FROM orders", "source": "pg", "ms": 5}),
        "not json",
        json.dumps({"sql": "SELECT 1 FROM old_orders", "source": "pg", "error": "no such table"}),
        json.dumps({"sql": "SELECT 1", "source": "pg", "colour": "red"}),
    ]) + "\n")
    res = QL.import_jsonl(db, f)
    assert res["added"] == 2 and [e["line"] for e in res["errors"]] == [2, 4]
    s = QL.stats(db)
    assert s["failed_runs"] == 1 and s["unresolved"] == [{"name": "old_orders", "runs": 1}]


def test_query_rows_survive_a_context_reindex(db):
    from keygraph.context import ContextDB
    QL.add(db, "SELECT id FROM orders", "pg", context="late parcels")
    with ContextDB(db) as ctx:
        ctx.reindex()
        assert all(kind != "query" for kind, _, _ in ctx.fts("late parcels"))
    assert QL.search(db, "parcels")["queries"]


def test_readers_never_create_tables(tmp_path):
    db = build_graph_dir(tmp_path)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE IF EXISTS query_runs")
    con.commit()
    con.close()
    stamp = db.read_bytes()
    assert QL.top(db)["queries"] == [] and QL.stats(db)["runs"] == 0
    assert db.read_bytes() == stamp


def test_context_and_error_are_masked(db):
    r = QL.add(db, "SELECT id FROM orders", "mysql", context="asked by ana@example.com",
               error="connect failed: mysql://app:hunter2@db.example:3306/shop")
    run = QL.show(db, r["fp"])["runs_list"][0]
    assert "ana@example.com" not in run["context"] and "hunter2" not in run["error"]


def test_double_quoted_secrets_never_reach_normalized_sql_or_search(db):
    r = QL.add(db, 'SELECT id FROM customers WHERE password = "hunter2" AND email = "ali@example.com"', "mysql")
    assert "hunter2" not in r["normalized_sql"] and "ali@example.com" not in r["normalized_sql"]
    con = sqlite3.connect(db)
    rows = " ".join(str(x) for row in con.execute("SELECT * FROM query_fingerprints") for x in row)
    rows += " ".join(str(x) for row in con.execute("SELECT body FROM search_fts WHERE kind='query'") for x in row)
    assert "hunter2" not in rows and "example.com" not in rows
