import json
from pathlib import Path

import pytest

from keygraph import doctor as D
from keygraph.cli import main
from tests.test_example_shop import _build


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    """The demo, built the way the README does (examples/shop/out is not committed)."""
    out = tmp_path_factory.mktemp("doctor_shop")
    _build(out)
    return out


def test_doctor_on_the_shop_example_is_ok_and_gives_an_absolute_launch_command(shop):
    r = D.run(shop)
    by = {c["name"]: c for c in r["checks"]}
    assert by["graph"]["status"] == by["query"]["status"] == "ok"
    assert r["graph"]["tables"] == 35
    m = r["mcp_launch"]
    assert Path(m["command"]).is_absolute() and m["args"][-2:] == ["--dir", str(shop.resolve())]
    if r["extras"]["mcp"]:
        assert by["mcp"]["status"] == "ok"


def test_doctor_without_a_graph_fails_with_a_fix(tmp_path):
    r = D.run(tmp_path / "none")
    graph = next(c for c in r["checks"] if c["name"] == "graph")
    assert r["status"] == "fail" and graph["status"] == "fail" and "keygraph scan" in graph["fix"]
    assert r["mcp_launch"] is None


def test_doctor_cli_exit_code_and_json(tmp_path, capsys):
    try:
        main(["doctor", "--dir", str(tmp_path), "--json"])
        code = 0
    except SystemExit as e:
        code = e.code
    assert code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_report_has_no_table_names_or_home_paths(shop, tmp_path):
    out = tmp_path / "bug.md"
    try:
        main(["doctor", "--dir", str(shop), "--report", str(out)])
    except SystemExit:
        pass
    text = out.read_text()
    assert "35 tables" in text and "keygraph:" in text
    for name in ("customers", "orders", "shipments", "shop.", str(Path.home())):
        assert name not in text


def test_query_check_ignores_self_references(shop, tmp_path):
    from keygraph.graph import Graph
    g = Graph.load(shop / "graph.json")
    self_ref = dict(g.edges[0], to=g.edges[0]["from"], confidence=1.0)
    g.data["edges"] = [self_ref] + g.edges
    out = tmp_path / "out"
    out.mkdir()
    g.save(out / "graph.json")
    by = {c["name"]: c for c in D.run(out)["checks"]}
    assert by["query"]["status"] == "ok"


def test_doctor_on_a_corrupt_graph_reports_a_failed_check(tmp_path, capsys):
    (tmp_path / "graph.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "keygraph.db").write_bytes(b"not a database")
    r = D.run(tmp_path)
    graph = next(c for c in r["checks"] if c["name"] == "graph")
    assert r["status"] == "fail" and graph["status"] == "fail" and "rebuild" in graph["fix"]
    report = tmp_path / "bug.md"
    try:
        main(["doctor", "--dir", str(tmp_path), "--report", str(report)])
    except SystemExit as exc:
        assert exc.code == 1
    assert report.exists() and "graph" in report.read_text(encoding="utf-8")
