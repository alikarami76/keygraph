"""keygraph queries add | import | top | slow | for | search | show | stats."""
import io
import json

import pytest

from keygraph.cli import main
from tests.codekit import build_graph_dir


@pytest.fixture()
def out(tmp_path):
    build_graph_dir(tmp_path / "out")
    return tmp_path / "out"


def run(capsys, *argv):
    capsys.readouterr()
    rc = main(list(argv))
    return rc, capsys.readouterr()


def test_add_then_read(out, tmp_path, capsys, monkeypatch):
    sql = tmp_path / "q.sql"
    for ms in (10, 20, 30, 40, 50, 60, 200):
        sql.write_text(f"SELECT id FROM orders WHERE status = {ms}\n")
        rc, o = run(capsys, "queries", "add", "--sql", str(sql), "--source", "mysql", "--ms", str(ms), "--context", "paid orders",
                    "--by", "ana", "--at", f"2026-11-01T10:00:{ms // 10:02d}", "--dir", str(out))
        assert rc == 0 and "(app.orders)" in o.out
    monkeypatch.setattr("sys.stdin", io.StringIO("SELECT email FROM customers"))
    rc, o = run(capsys, "queries", "add", "--sql", "-", "--source", "pg", "--ms", "5", "--dir", str(out), "--json")
    fp2 = json.loads(o.out)["fp"]
    rc, o = run(capsys, "queries", "slow", "--dir", str(out))
    assert "7 runs, 1 people, median 40 ms, p95 158 ms, last 5 median 50 ms vs 15 ms before" in o.out
    rc, o = run(capsys, "queries", "top", "--table", "customers", "--dir", str(out), "--json")
    assert [q["fp"] for q in json.loads(o.out)["queries"]] == [fp2]
    rc, o = run(capsys, "queries", "for", "orders", "--dir", str(out))
    assert "why: paid orders" in o.out
    rc, o = run(capsys, "queries", "search", "paid", "--dir", str(out))
    assert "select id from orders where status = ?" in o.out
    rc, o = run(capsys, "queries", "show", fp2[:8], "--dir", str(out))
    assert rc == 0 and "app.customers" in o.out and "run 8" in o.out
    rc, o = run(capsys, "queries", "stats", "--dir", str(out))
    assert o.out.startswith("8 runs, 2 distinct queries")


def test_import_and_errors(out, tmp_path, capsys):
    f = tmp_path / "q.jsonl"
    f.write_text(json.dumps({"sql": "SELECT 1 FROM orders", "source": "pg"}) + "\n{oops\n")
    rc, o = run(capsys, "queries", "import", str(f), "--dir", str(out))
    assert rc == 0 and "imported 1 runs" in o.out and "line 2:" in o.out
    rc, o = run(capsys, "queries", "show", "zzzz", "--dir", str(out))
    assert rc == 2 and "no query fingerprint starts with 'zzzz'" in o.err
    rc, o = run(capsys, "queries", "top", "--by", "people", "--table", "nosuch", "--dir", str(out))
    assert rc == 2 and "unknown table" in o.err
