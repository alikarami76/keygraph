"""index_repo end to end: incremental re-index, unresolved names, masking, caps, determinism, where / what / stats."""
import shutil
import sqlite3

import pytest

from keygraph import code
from tests.codekit import CODE_FIX, build_graph_dir


@pytest.fixture()
def db(tmp_path):
    return build_graph_dir(tmp_path / "out")


def rows(db, table="code_refs"):
    con = sqlite3.connect(db)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        con.close()


def test_index_where_what_stats(db):
    res = code.index_repo(db, CODE_FIX / "php-doctrine", "php", now="2026-11-04T00:00:00")
    assert res["files"] == 3 and res["changed"] == 3 and res["unresolved"] == 1 and res["capped"] == {}
    w = code.where(db, "orders.status")
    assert w["table"] == "app.orders" and w["column"] == "status"
    assert [(r["kind"], r["line"], r["symbol"]) for r in w["refs"]] == [
        ("declare_column", 19, "status"), ("use", 21, "getStatus"), ("use", 23, "status")]
    assert w["counts"]["use"] == 2 and w["total"] == 3
    one = code.what(db, path="Order.php", line=23)["files"][0]
    assert [r["kind"] for r in one["refs"]] == ["use"]
    assert one["entity"]["table"] == "app.orders" and one["entity"]["symbol"] == "Order"
    assert [r["column"] for r in one["entity"]["refs"]] == [None, "id", "customer_id", "status"]
    assert code.what(db, symbol="trackingNumber")["refs"][0]["column"] == "tracking_no"
    s = code.stats(db)
    assert s["repos"][0]["repo"] == "php" and s["repos"][0]["root"] == "php-doctrine"
    assert s["unresolved"] == [{"repo": "php", "path": "Legacy.php", "line": 3, "kind": "declare_table",
                                "name": "legacy_orders", "rule": "doctrine-table-attribute"}]
    with pytest.raises(ValueError, match="unknown kind"):
        code.where(db, "orders", kind="nope")


def test_incremental_reindex_changes_and_deletes(db, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(CODE_FIX / "php-doctrine", repo)
    code.index_repo(db, repo, "php")
    (repo / "Shipment.php").unlink()
    text = (repo / "Order.php").read_text().replace("private string $status;", "private string $status;\n\n    #[ORM\\Column]\n    private string $total;")
    (repo / "Order.php").write_text(text)
    res = code.index_repo(db, repo, "php")
    assert (res["changed"], res["deleted"], res["files"]) == (1, 1, 2)
    assert code.where(db, "shipments")["refs"] == []
    assert [r["line"] for r in code.where(db, "orders.total", kind="declare_column")["refs"]] == [22]
    again = code.index_repo(db, repo, "php")
    assert again["changed"] == 0 and again["refs"] == res["refs"]


def test_new_rules_force_a_full_reindex(db, tmp_path):
    code.index_repo(db, CODE_FIX / "custom-pattern", "c")
    assert code.where(db, "bug_reports")["refs"] == []  # no patterns yet: nothing declares the table
    shutil.copy(CODE_FIX / "custom-pattern" / "code-patterns.yaml", db.parent / "code-patterns.yaml")
    pytest.importorskip("yaml")
    res = code.index_repo(db, CODE_FIX / "custom-pattern", "c")
    assert res["changed"] == 1
    kinds = [(r["kind"], r["column"], r["rule"]) for r in code.where(db, "bug_reports.title")["refs"]]
    assert kinds == [("declare_column", "title", "fiction-column"), ("use", "title", "fiction-field"),
                     ("use", "title", "use:symbol")]


def test_snippets_are_masked(db, tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "q.py").write_text('db.run("SELECT id FROM orders WHERE token = \'abc\'", password="hunter2")\n')
    code.index_repo(db, repo, "r")
    snip = code.where(db, "orders")["refs"][0]["snippet"]
    assert "hunter2" not in snip and 'password="***"' in snip


def test_use_cap_is_reported(db, tmp_path):
    repo = tmp_path / "r"
    shutil.copytree(CODE_FIX / "go-gorm", repo)
    (repo / "use.go").write_text("package models\n" + "".join(f"var x{i} = o.CustomerID\n" for i in range(8)))
    res = code.index_repo(db, repo, "go", max_uses=5)
    assert res["capped"] == {"app.orders.customer_id": 8}
    w = code.where(db, "orders.customer_id", kind="use")
    assert w["counts"]["use"] == 5 and len(w["refs"]) == 5  # the first five, in path and line order
    assert [r["line"] for r in w["refs"]] == [2, 3, 4, 5, 6]


def test_two_runs_give_identical_rows(tmp_path):
    out = []
    for name in ("a", "b"):
        db = build_graph_dir(tmp_path / name)
        for repo in ("php-doctrine", "raw-sql", "ts-typeorm", "python-django"):
            code.index_repo(db, CODE_FIX / repo, repo, now="T")
        out.append((rows(db), rows(db, "code_unresolved"), rows(db, "code_files")))
    assert out[0] == out[1] and out[0][0]


def test_needs_a_graph_and_a_directory(tmp_path):
    with pytest.raises(ValueError, match="no graph at"):
        code.index_repo(tmp_path / "keygraph.db", CODE_FIX / "go-gorm")
    db = build_graph_dir(tmp_path / "out")
    with pytest.raises(ValueError, match="is not a directory"):
        code.index_repo(db, tmp_path / "missing")
