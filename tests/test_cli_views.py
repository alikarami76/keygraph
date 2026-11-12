import json
import shutil

import pytest

from conftest import needs_yaml
from keygraph.cli import main

FIX = "tests/fixtures"


def _load(tmp_path, *extra):
    return main(["load", "--columns", f"{FIX}/shop_columns.json", "--statistics", f"{FIX}/shop_statistics.json",
                 "--key-columns", f"{FIX}/shop_key_columns.json", "--dir", str(tmp_path), *extra])


def test_build_writes_areas_and_docs(tmp_path, capsys):
    assert _load(tmp_path) == 0
    data = json.loads((tmp_path / "graph.json").read_text())
    assert data["areas"] and "hubs" in data and all("hub" in e for e in data["edges"])
    assert (tmp_path / "areas" / "index.md").exists() and any((tmp_path / "tables").iterdir())
    capsys.readouterr()
    assert main(["areas", "--dir", str(tmp_path), "--json"]) == 0
    areas = json.loads(capsys.readouterr().out)
    assert areas[0]["tables"]
    assert main(["area", "orders", "--dir", str(tmp_path), "--json"]) == 0
    a = json.loads(capsys.readouterr().out)
    assert "shop.orders" in a["tables"] and "neighbours" in a
    assert {"id", "name", "tables", "internal", "neighbours", "hub_labels"} <= set(a)
    # by area id and by case-insensitive name give the same area
    capsys.readouterr()
    assert main(["area", a["id"], "--dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == a["id"]
    assert main(["area", a["name"].upper(), "--dir", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == a["id"]
    assert main(["area", "orders", "--dir", str(tmp_path)]) == 0
    assert "shop.orders" in capsys.readouterr().out


def test_area_unknown_fails_with_suggestions(tmp_path, capsys):
    _load(tmp_path)
    capsys.readouterr()
    assert main(["area", "zzzz_nothing", "--dir", str(tmp_path)]) == 2
    assert "keygraph:" in capsys.readouterr().err


def test_describe_has_relation_summary(tmp_path, capsys):
    _load(tmp_path)
    capsys.readouterr()
    assert main(["describe", "orders", "--dir", str(tmp_path), "--json"]) == 0
    assert "relation_summary" in json.loads(capsys.readouterr().out)


def test_rebuild_removes_stale_pages(tmp_path):
    _load(tmp_path)
    (tmp_path / "areas" / "old-name.md").write_text("stale")
    (tmp_path / "tables" / "gone.t.md").write_text("stale")
    mf = tmp_path / ".keygraph-pages.json"  # pages the previous build wrote
    mf.write_text(json.dumps(json.loads(mf.read_text()) + ["areas/old-name.md", "tables/gone.t.md"]))
    _load(tmp_path)
    assert not (tmp_path / "areas" / "old-name.md").exists()
    assert not (tmp_path / "tables" / "gone.t.md").exists()
    assert (tmp_path / "areas" / "index.md").exists()


def test_rebuild_keeps_user_files_and_ignores_symlinked_dirs(tmp_path):
    _load(tmp_path)
    (tmp_path / "tables" / "notes.txt").write_text("mine")
    (tmp_path / "areas" / "mine").mkdir()
    (tmp_path / "areas" / "mine" / "x.md").write_text("mine")
    (tmp_path / "areas" / "renamed-away.md").write_text("stale")
    mf = tmp_path / ".keygraph-pages.json"
    mf.write_text(json.dumps(json.loads(mf.read_text()) + ["areas/renamed-away.md", "areas/mine/x.md"]))
    _load(tmp_path)
    assert (tmp_path / "tables" / "notes.txt").read_text() == "mine"
    assert (tmp_path / "areas" / "mine" / "x.md").read_text() == "mine"
    assert not (tmp_path / "areas" / "renamed-away.md").exists()
    # a symlinked tables dir pointing outside is never deleted through
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    outside.mkdir()
    (outside / "gone.t.md").write_text("keep")
    shutil.rmtree(tmp_path / "tables")
    (tmp_path / "tables").symlink_to(outside)
    mf.write_text(json.dumps(json.loads(mf.read_text()) + ["tables/gone.t.md"]))
    _load(tmp_path)
    assert (outside / "gone.t.md").read_text() == "keep"


@needs_yaml
def test_bad_hub_names_warn_and_stale_curation_is_dropped(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "c.yaml").write_text("hubs:\n  include: [no_such_table]\nareas:\n  - name: Pinned Area\n    pin: [orders]\n")
    assert _load(tmp_path / "out", "--context", str(ctx)) == 0
    data = json.loads((tmp_path / "out" / "graph.json").read_text())
    assert any("no_such_table" in w for w in data["warnings"])
    assert any(a["name"] == "Pinned Area" for a in data["areas"])
    # rebuild importing a different file: the old file's rules must not apply
    other = tmp_path / "other"
    other.mkdir()
    (other / "d.yaml").write_text("concepts: []\n")
    assert _load(tmp_path / "out", "--context", str(other)) == 0
    data = json.loads((tmp_path / "out" / "graph.json").read_text())
    assert not any(a["name"] == "Pinned Area" for a in data["areas"])
    assert not any("no_such_table" in w for w in data["warnings"])


@pytest.mark.parametrize("body", ["[1]", "[null]", "null", "5", '{"a":1}'])
def test_malformed_manifest_does_not_break_build(tmp_path, body):
    _load(tmp_path)
    (tmp_path / "tables" / "notes.txt").write_text("mine")
    (tmp_path / "areas" / "keep.md").write_text("mine")
    (tmp_path / ".keygraph-pages.json").write_text(body)
    assert _load(tmp_path) == 0
    assert (tmp_path / "tables" / "notes.txt").read_text() == "mine"
    assert (tmp_path / "areas" / "keep.md").read_text() == "mine"
    assert isinstance(json.loads((tmp_path / ".keygraph-pages.json").read_text()), list)
