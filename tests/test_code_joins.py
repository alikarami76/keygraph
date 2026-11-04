"""Links seen in code: join extractor, code_joins in the index, and the merge into the graph at render."""
import json
import shutil
import sqlite3

import pytest

from keygraph import code
from keygraph.cli import main
from keygraph.code.sql import sql_joins
from keygraph.code.vocab import Vocab
from tests.codekit import TABLES, build_graph_dir

O, C, I, S = "app.orders", "app.customers", "app.order_items", "app.shipments"
VOCAB = Vocab({f"app.{t}": c for t, c in TABLES.items()})


def test_on_join_with_aliases():
    pytest.importorskip("sqlglot")
    sql = "SELECT * FROM orders o JOIN customers c ON c.id = o.customer_id"
    assert sql_joins(sql, VOCAB) == [(C, "id", O, "customer_id")]


def test_implicit_where_join_and_literals_ignored():
    pytest.importorskip("sqlglot")
    sql = ("SELECT 1 FROM orders o, order_items i WHERE i.order_id = o.id AND o.status = 'paid' AND i.qty > o.total "
           "AND i.product_id = 5 AND i.nope = o.id")
    assert sql_joins(sql, VOCAB) == [(I, "order_id", O, "id")]


def test_self_join_and_unresolved_alias():
    pytest.importorskip("sqlglot")
    assert sql_joins("SELECT 1 FROM orders a JOIN orders b ON a.id = b.customer_id", VOCAB) == [(O, "customer_id", O, "id")]
    assert sql_joins("SELECT 1 FROM orders a JOIN (SELECT id FROM customers) q ON q.id = a.customer_id", VOCAB) == []


def test_no_sqlglot_gives_nothing(monkeypatch):
    from keygraph.code import sql
    monkeypatch.setattr(sql, "sqlglot", None)
    assert sql.sql_joins("SELECT 1 FROM orders o JOIN customers c ON c.id = o.customer_id", VOCAB) == []


@pytest.fixture()
def out(tmp_path):
    build_graph_dir(tmp_path)
    return tmp_path


def make_repo(tmp_path, body):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "reports.py").write_text(body)
    return repo


JOIN_PY = ('Q1 = """SELECT o.id FROM orders o\n  JOIN shipments s ON s.order_id = o.id"""\n'
           'Q2 = "SELECT 1 FROM orders o JOIN customers c ON c.id = o.customer_id"\n')


def joins(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT * FROM code_joins ORDER BY rowid").fetchall()
    finally:
        con.close()


def test_index_stores_joins_incrementally(out, tmp_path):
    pytest.importorskip("sqlglot")
    db = out / "keygraph.db"
    repo = make_repo(tmp_path, JOIN_PY)
    code.index_repo(db, repo, "api")
    assert sorted(joins(db)) == sorted([("api", "reports.py", 2, O, "id", S, "order_id"),
                                        ("api", "reports.py", 3, C, "id", O, "customer_id")])
    code.index_repo(db, repo, "api")
    assert len(joins(db)) == 2
    (repo / "reports.py").write_text('Q = "SELECT 1 FROM orders o JOIN shipments s ON s.order_id = o.id"\n')
    code.index_repo(db, repo, "api")
    assert joins(db) == [("api", "reports.py", 1, O, "id", S, "order_id")]
    (repo / "reports.py").unlink()
    code.index_repo(db, repo, "api")
    assert joins(db) == []


def graph(out):
    return json.loads((out / "graph.json").read_text())


def edge(g, frm, fcol, to, tcol):
    return next((e for e in g["edges"] if (e["from"], e["from_columns"], e["to"], e["to_columns"]) == (frm, [fcol], to, [tcol])), None)


def render(capsys, out):
    assert main(["render", "--dir", str(out)]) == 0


def test_render_adds_and_boosts_edges(out, tmp_path, capsys):
    pytest.importorskip("sqlglot")
    db = out / "keygraph.db"
    before = edge(graph(out), O, "customer_id", C, "id")
    code.index_repo(db, make_repo(tmp_path, JOIN_PY), "api")
    render(capsys, out)
    g = graph(out)
    assert edge(g, S, "order_id", O, "id")["confidence"] >= 0.95
    (tmp_path / "repo" / "more.py").write_text('Q = "SELECT 1 FROM shipments s JOIN products p ON s.carrier = p.id"\n')
    code.index_repo(db, tmp_path / "repo", "api")
    render(capsys, out)
    g = graph(out)
    new = edge(g, S, "carrier", "app.products", "id")
    assert new["kind"] == "inferred" and new["source"] == "code" and new["confidence"] == 0.9
    assert new["evidence"] == ["joined in code 1 times, e.g. api:more.py:1"]
    assert new["cardinality"] == "many-to-one" and new["id"] == f"{S}(carrier)->app.products(id)"
    after = edge(g, O, "customer_id", C, "id")
    assert after["confidence"] == max(before["confidence"], 0.95)
    assert after["evidence"][-1].startswith("joined in code 1 times") and "source" not in after
    assert g["stats"]["edges"] == len(g["edges"])


def test_boost_existing_edge(out, tmp_path, capsys):
    pytest.importorskip("sqlglot")
    g = graph(out)
    e = edge(g, O, "customer_id", C, "id")
    assert e is not None
    code.index_repo(out / "keygraph.db", make_repo(tmp_path, JOIN_PY), "api")
    render(capsys, out)
    e2 = edge(graph(out), O, "customer_id", C, "id")
    assert e2["confidence"] >= 0.95 and any("joined in code" in x for x in e2["evidence"])


def test_render_is_idempotent_and_deterministic(out, tmp_path, capsys):
    pytest.importorskip("sqlglot")
    code.index_repo(out / "keygraph.db", make_repo(tmp_path, JOIN_PY), "api")
    render(capsys, out)
    first = (out / "graph.json").read_text()
    render(capsys, out)
    assert (out / "graph.json").read_text() == first
    other = tmp_path / "other"
    build_graph_dir(other)
    code.index_repo(other / "keygraph.db", tmp_path / "repo", "api")
    main(["render", "--dir", str(other)])
    assert (other / "graph.json").read_text() == first


def test_stale_merge_is_undone_when_joins_vanish(out, tmp_path, capsys):
    pytest.importorskip("sqlglot")
    repo = make_repo(tmp_path, JOIN_PY)
    plain = (out / "graph.json").read_text()
    code.index_repo(out / "keygraph.db", repo, "api")
    render(capsys, out)
    (repo / "reports.py").unlink()
    code.index_repo(out / "keygraph.db", repo, "api")
    render(capsys, out)
    assert json.loads((out / "graph.json").read_text())["edges"] == json.loads(plain)["edges"]


def test_logged_queries_add_evidence(out, capsys):
    pytest.importorskip("sqlglot")
    from keygraph import queries as QL
    QL.add(out / "keygraph.db", "SELECT 1 FROM orders o JOIN customers c ON c.id = o.customer_id", "mysql", at="2026-11-01T10:00:00")
    QL.add(out / "keygraph.db", "SELECT 2 FROM orders o JOIN customers c ON c.id = o.customer_id", "mysql", at="2026-11-02T10:00:00")
    render(capsys, out)
    e = edge(graph(out), O, "customer_id", C, "id")
    assert e["evidence"][-1] == "joined in 2 logged queries"
