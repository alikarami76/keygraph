"""keygraph code index | where | what | stats."""
import json

import pytest

from keygraph.cli import main
from tests.codekit import CODE_FIX, build_graph_dir


@pytest.fixture()
def out(tmp_path):
    build_graph_dir(tmp_path)
    return tmp_path


def run(capsys, *argv):
    capsys.readouterr()
    code = main(list(argv))
    return code, capsys.readouterr()


def test_index_two_repos_then_query(out, capsys):
    rc, o = run(capsys, "code", "index", str(CODE_FIX / "php-doctrine"), str(CODE_FIX / "go-gorm"), "--name", "php",
                "--name", "go", "--dir", str(out))
    assert rc == 0 and "php: 3 files" in o.out and "go: 1 files" in o.out
    rc, o = run(capsys, "code", "where", "orders.status", "--dir", str(out), "--json")
    got = json.loads(o.out)
    assert [(r["repo"], r["kind"]) for r in got["refs"]] == [("go", "declare_column"), ("php", "declare_column"),
                                                            ("php", "use"), ("php", "use")]
    rc, o = run(capsys, "code", "where", "orders.status", "--kind", "use", "--dir", str(out))
    assert rc == 0 and "php:Order.php:21  use" in o.out and "go:" not in o.out.split("\n", 1)[1]
    rc, o = run(capsys, "code", "what", "Order.php:19", "--dir", str(out))
    assert "entity Order -> app.orders (declared on line 8)" in o.out
    rc, o = run(capsys, "code", "what", "--symbol", "State", "--dir", str(out), "--json")
    assert json.loads(o.out)["refs"][0]["column"] == "status"
    rc, o = run(capsys, "code", "stats", "--out", str(out))
    assert rc == 0 and o.out.startswith("go: 1 files")
    assert (out / "keygraph.html").exists()


def test_errors(out, capsys):
    rc, o = run(capsys, "code", "index", str(CODE_FIX / "go-gorm"), "--name", "a", "--name", "b", "--dir", str(out))
    assert rc == 2 and "one --name per repo" in o.err
    rc, o = run(capsys, "code", "where", "nosuch", "--dir", str(out))
    assert rc == 2 and "unknown table" in o.err
    rc, o = run(capsys, "code", "what", "--dir", str(out))
    assert rc == 2 and "PATH[:LINE] or --symbol" in o.err
    with pytest.raises(SystemExit, match="no graph"):
        main(["code", "stats", "--dir", str(out / "empty")])
