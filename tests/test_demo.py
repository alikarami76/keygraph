"""keygraph demo: the bundled webshop, built in one command with no extras and no database."""

import filecmp
import json
import shlex
import sys
import zipfile
from pathlib import Path

import pytest

from keygraph.cli import main

SHOP = Path(__file__).resolve().parents[1] / "examples" / "shop"
PKG = Path(__file__).resolve().parents[1] / "src" / "keygraph" / "demo"


@pytest.fixture(autouse=True)
def _fixed_time(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")


@pytest.fixture
def no_browser(monkeypatch):
    calls = []
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: calls.append(a) or True)
    return calls


def _demo(tmp_path, capsys, *extra):
    capsys.readouterr()
    assert main(["demo", "--dir", str(tmp_path / "d"), "--no-open", "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_demo_builds_explorer_and_graph(tmp_path, capsys):
    r = _demo(tmp_path, capsys)
    d = tmp_path / "d"
    assert (d / "graph.json").is_file() and (d / "keygraph.html").is_file()
    assert r["dir"] == str(d) and r["explorer"] == str(d / "keygraph.html")
    assert r["tables"] >= 30 and r["relations"] > 0 and isinstance(r["skipped"], list)
    assert len(r["next"]) == 3


def test_demo_no_open_never_touches_the_browser(tmp_path, capsys, no_browser):
    _demo(tmp_path, capsys)
    assert no_browser == []


def test_demo_opens_the_explorer_by_default(tmp_path, capsys, no_browser, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    capsys.readouterr()
    assert main(["demo", "--dir", str(tmp_path / "d")]) == 0
    assert len(no_browser) == 1 and no_browser[0][0].endswith("keygraph.html")


def test_demo_does_not_open_in_ci(tmp_path, capsys, no_browser, monkeypatch):
    monkeypatch.setenv("CI", "1")
    capsys.readouterr()
    assert main(["demo", "--dir", str(tmp_path / "d")]) == 0
    assert no_browser == []


def test_demo_refuses_a_folder_that_is_not_a_demo(tmp_path, capsys):
    d = tmp_path / "d"
    d.mkdir()
    (d / "mine.txt").write_text("keep me")
    assert main(["demo", "--dir", str(d), "--no-open"]) == 2
    assert "not a keygraph demo" in capsys.readouterr().err
    assert [p.name for p in d.iterdir()] == ["mine.txt"]


def test_demo_accepts_an_empty_folder(tmp_path, capsys):
    (tmp_path / "d").mkdir()
    _demo(tmp_path, capsys)
    assert (tmp_path / "d" / "graph.json").is_file()


def test_demo_rerun_is_byte_identical(tmp_path, capsys):
    _demo(tmp_path, capsys)
    first = {n: (tmp_path / "d" / n).read_bytes() for n in ("graph.json", "keygraph.html", "RELATIONS_REPORT.md", "build.json")}
    _demo(tmp_path, capsys)
    assert first == {n: (tmp_path / "d" / n).read_bytes() for n in first}
    other = tmp_path / "e"
    capsys.readouterr()
    assert main(["demo", "--dir", str(other), "--no-open", "--json"]) == 0
    assert (other / "graph.json").read_bytes() == first["graph.json"]
    assert (other / "keygraph.html").read_bytes() == first["keygraph.html"]


def test_demo_without_any_extra_still_works(tmp_path, capsys, monkeypatch):
    """Core-only install: no PyYAML, SQLAlchemy or sqlglot. Context ships as JSON, profiles use sqlite3."""
    for mod in ("yaml", "sqlalchemy", "sqlglot"):
        monkeypatch.setitem(sys.modules, mod, None)
    monkeypatch.delitem(sys.modules, "keygraph.code.sql", raising=False)
    r = _demo(tmp_path, capsys)
    g = json.loads((tmp_path / "d" / "graph.json").read_text())
    assert (tmp_path / "d" / "keygraph.html").is_file()
    assert g["profiles"]["shop.orders.status"]["reconcile"]["undocumented_codes"] == ["9"]
    assert any("code" in s for s in r["skipped"]) or r["skipped"] == []


def test_demo_next_commands_run(tmp_path, capsys):
    r = _demo(tmp_path, capsys)
    assert [shlex.split(c)[1] for c in r["next"]] == ["path", "describe", "mcp"]
    for cmd in r["next"][:2]:
        argv = shlex.split(cmd)
        assert argv[0] == "keygraph"
        capsys.readouterr()
        assert main(argv[1:]) == 0
        assert capsys.readouterr().out.strip()
    mcp = shlex.split(r["next"][2])
    assert mcp[2:] == ["--dir", str(tmp_path / "d")]


def test_demo_text_output_names_explorer_and_commands(tmp_path, capsys):
    capsys.readouterr()
    assert main(["demo", "--dir", str(tmp_path / "d"), "--no-open"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "d" / "keygraph.html") in out and "keygraph path customers shipments" in out


def test_bundled_demo_data_matches_examples_shop():
    """examples/shop is the source; src/keygraph/demo is the packaged copy and must not drift."""
    for rel in ("dumps/columns.json", "dumps/key_columns.json", "dumps/statistics.json", "dumps/tables.json",
                "queries.jsonl", "shop.db", "app/README.md", "app/shop/models.py", "app/shop/orders.py",
                "app/sql/reports.sql", "app/web/shipment.ts"):
        assert filecmp.cmp(SHOP / rel, PKG / rel, shallow=False), rel


def test_bundled_context_json_matches_context_yaml():
    yaml = pytest.importorskip("yaml", reason="needs PyYAML (extra: yaml)")
    assert json.loads((PKG / "context.json").read_text()) == yaml.safe_load((SHOP / "context.yaml").read_text())


def test_wheel_contains_the_demo_files(tmp_path):
    hatchling = pytest.importorskip("hatchling.build", reason="needs hatchling to build a wheel")
    import os
    cwd = os.getcwd()
    os.chdir(Path(__file__).resolve().parents[1])
    try:
        name = hatchling.build_wheel(str(tmp_path))
    finally:
        os.chdir(cwd)
    names = set(zipfile.ZipFile(tmp_path / name).namelist())
    for rel in ("context.json", "queries.jsonl", "shop.db", "dumps/columns.json", "app/shop/models.py"):
        assert f"keygraph/demo/{rel}" in names, rel
