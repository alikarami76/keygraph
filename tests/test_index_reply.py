"""Index replies stay under a hard character cap: compact JSON on both surfaces, then deterministic trimming with a note."""

import asyncio
import json

import pytest

from keygraph.cli import main
from keygraph.index import cli_api as X
from keygraph.index import reply as RY

TABLES, COLS = 320, 42


def _wide(d):
    """A wide warehouse: 320 tables of 42 commented columns (distinct names, so no family forms), each pointing at a parent by declared foreign key."""
    cols, stats, keys, tabs = [], [], [], []
    for t in range(TABLES):
        name = f"fact_{t:03d}"
        tabs.append({"TABLE_SCHEMA": "dw", "TABLE_NAME": name, "ENGINE": "InnoDB", "TABLE_ROWS": 1000 + t,
                     "TABLE_COMMENT": f"Fact table number {t} holding measured values for the reporting layer"})
        for i in range(COLS):
            cname = "id" if i == 0 else f"parent_id" if i == 1 else f"m{t:03d}_{i:02d}"
            cols.append({"TABLE_SCHEMA": "dw", "TABLE_NAME": name, "COLUMN_NAME": cname, "ORDINAL_POSITION": i + 1,
                         "DATA_TYPE": "int" if i < 2 else "varchar", "COLUMN_TYPE": "int" if i < 2 else "varchar(40)",
                         "IS_NULLABLE": "NO" if i == 0 else "YES", "COLUMN_KEY": "PRI" if i == 0 else "",
                         "COLUMN_COMMENT": f"{cname} of {name}: a long free text description that fills the packet " * 2})
        stats.append({"TABLE_SCHEMA": "dw", "TABLE_NAME": name, "INDEX_NAME": "PRIMARY", "NON_UNIQUE": 0, "SEQ_IN_INDEX": 1,
                      "COLUMN_NAME": "id"})
        if t:
            keys.append({"TABLE_SCHEMA": "dw", "TABLE_NAME": name, "CONSTRAINT_NAME": f"fk_{t}", "ORDINAL_POSITION": 1,
                         "COLUMN_NAME": "parent_id", "REFERENCED_TABLE_SCHEMA": "dw",
                         "REFERENCED_TABLE_NAME": f"fact_{(t - 1) // 3:03d}", "REFERENCED_COLUMN_NAME": "id"})
    paths = {}
    for k, v in (("columns", cols), ("statistics", stats), ("key-columns", keys), ("tables", tabs)):
        paths[k] = d / f"{k}.json"
        paths[k].write_text(json.dumps(v), encoding="utf-8")
    argv = ["load", "--dir", str(d / "map")]
    for k, p in paths.items():
        argv += [f"--{k}", str(p)]
    assert main(argv) == 0
    return d / "map"


@pytest.fixture(scope="module")
def wide(tmp_path_factory):
    return _wide(tmp_path_factory.mktemp("wide"))


@pytest.fixture
def work(wide, tmp_path):
    import shutil
    out = tmp_path / "map"
    shutil.copytree(wide, out)
    return out


def _json(capsys, out, *argv):
    capsys.readouterr()
    assert main([*argv, "--dir", str(out), "--json"]) == 0
    return capsys.readouterr().out


def test_dumps_is_compact_and_keeps_unicode():
    assert RY.dumps({"a": [1, 2], "b": "é"}) == '{"a":[1,2],"b":"é"}'


def test_cap_defaults_to_40000_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv(RY.ENV, raising=False)
    assert RY.cap() == 40_000
    monkeypatch.setenv(RY.ENV, "25000")
    assert RY.cap() == 25_000
    monkeypatch.setenv(RY.ENV, "nonsense")
    assert RY.cap() == 40_000
    monkeypatch.setenv(RY.ENV, "10")  # a cap too small to hold any reply is raised to the floor
    assert RY.cap() == RY.MIN_CAP


def test_every_reply_on_a_wide_map_is_under_the_default_cap_in_compact_json(work, capsys, monkeypatch):
    monkeypatch.delenv(RY.ENV, raising=False)
    start = _json(capsys, work, "index", "start")
    assert len(start) < 40_000 and "\n" not in start.strip()
    text = _json(capsys, work, "index", "next")
    r = json.loads(text)
    assert r["status"] == "unit" and len(text.strip()) < 40_000 and "\n" not in text.strip()
    assert text.strip() == RY.dumps(r)
    assert len(_json(capsys, work, "index", "status")) < 40_000


def test_largest_reply_over_many_units_stays_under_the_cap(work, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(RY.ENV, raising=False)
    X.start_result(work)
    answer = tmp_path / "a.json"
    answer.write_text(json.dumps({"relations": [], "columns": [], "tables": [], "concepts": [], "metrics": [], "performance": []}),
                      encoding="utf-8")
    biggest = 0
    for _ in range(12):
        text = _json(capsys, work, "index", "next")
        biggest = max(biggest, len(text.strip()))
        r = json.loads(text)
        assert len(text.strip()) < 40_000
        assert "unit_id" in r, r
        sub = _json(capsys, work, "index", "submit", r["unit_id"], str(answer), "--model", "m")
        assert len(sub.strip()) < 40_000
    assert biggest > 15_000  # the fixture is wide enough to matter


def test_a_small_cap_trims_lowest_priority_packet_sections_and_says_so(work, monkeypatch):
    monkeypatch.setenv(RY.ENV, "40000")
    X.start_result(work)
    full = X.next_result(work)
    assert "truncated" not in full
    size = len(RY.dumps(full))
    monkeypatch.setenv(RY.ENV, str(size - 3000))
    cut = X.next_result(work)
    assert len(RY.dumps(cut)) <= size - 3000
    t = cut["truncated"]
    assert t["cap"] == size - 3000 and t["original_chars"] == size and t["cut"]
    assert all(isinstance(x, str) for x in t["cut"])
    # the lowest-priority sections go first: the table shapes are still all there
    assert [x["table"] for x in cut["packet"]["tables"]] == [x["table"] for x in full["packet"]["tables"]]
    assert cut["unit_id"] == full["unit_id"] and cut["answer_schema"] == full["answer_schema"] and cut["instructions"] == full["instructions"]
    assert X.next_result(work) == cut  # deterministic


def test_trimming_never_changes_the_token_accounting(work, monkeypatch):
    from keygraph.context import ContextDB
    from keygraph.index import jobs

    def charged():
        with ContextDB(work / "keygraph.db") as ctx:
            u = jobs.next_unit(ctx, jobs.open_job(ctx)["job_id"])
        return u["tokens_est"], u["packet_hash"]
    monkeypatch.setenv(RY.ENV, "40000")
    X.start_result(work)
    full = X.next_result(work)
    before = charged()
    monkeypatch.setenv(RY.ENV, str(len(RY.dumps(full)) - 4000))
    assert "truncated" in X.next_result(work)
    assert charged() == before and full["packet"]["cap"] == 6000


def test_a_reply_that_cannot_fit_says_so(work, monkeypatch):
    monkeypatch.setenv(RY.ENV, str(RY.MIN_CAP))
    X.start_result(work)
    r = X.next_result(work)
    assert r["truncated"]["cut"] and (len(RY.dumps(r)) <= RY.MIN_CAP or r["truncated"].get("over_cap") is True)


def test_cli_and_mcp_give_identical_text_and_a_note_when_trimmed(work, tmp_path, capsys, monkeypatch):
    pytest.importorskip("mcp")
    import shutil

    from keygraph.mcp_server import build_server
    mcp_dir = tmp_path / "mcp"
    shutil.copytree(work, mcp_dir)
    monkeypatch.setenv(RY.ENV, "12000")
    s = build_server(mcp_dir)

    def call(name, **kw):
        return asyncio.run(s.call_tool(name, kw)).content[0].text

    cli_start = _json(capsys, work, "index", "start").strip()
    assert call("index_start") == cli_start
    cli_next = _json(capsys, work, "index", "next").strip()
    mcp_next = call("index_next")
    assert mcp_next == cli_next and "\n" not in mcp_next
    assert len(mcp_next) <= 12_000 and "truncated" in json.loads(mcp_next)
    assert call("index_status") == _json(capsys, work, "index", "status").strip()


def test_family_copies_get_no_unit_and_a_note_in_the_start_reply_and_the_packet(tmp_path, capsys):
    """Three archive-style copies share the columns of one table: one unit, a families.json note, a `copies` packet section."""
    cols, tabs = [], []
    for name, rows in (("orders", 900), ("orders_archive", 900), ("stg_orders", 500), ("customers", 40)):
        tabs.append({"TABLE_SCHEMA": "dw", "TABLE_NAME": name, "ENGINE": "InnoDB", "TABLE_ROWS": rows})
        for i, c in enumerate(("id", "customer_ref", "total", "placed_at") if name != "customers" else ("id", "full_name", "tier")):
            cols.append({"TABLE_SCHEMA": "dw", "TABLE_NAME": name, "COLUMN_NAME": c, "ORDINAL_POSITION": i + 1, "DATA_TYPE": "int",
                         "COLUMN_TYPE": "int", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI" if c == "id" else ""})
    stats = [{"TABLE_SCHEMA": "dw", "TABLE_NAME": t["TABLE_NAME"], "INDEX_NAME": "PRIMARY", "NON_UNIQUE": 0, "SEQ_IN_INDEX": 1,
              "COLUMN_NAME": "id"} for t in tabs]
    argv = ["load", "--dir", str(tmp_path / "map")]
    for k, v in (("columns", cols), ("statistics", stats), ("tables", tabs)):
        (tmp_path / f"{k}.json").write_text(json.dumps(v), encoding="utf-8")
        argv += [f"--{k}", str(tmp_path / f"{k}.json")]
    assert main(argv) == 0
    out = tmp_path / "map"
    start = json.loads(_json(capsys, out, "index", "start"))
    assert start["units"] == 2 and start["families"] == 1 and start["copies_without_unit"] == 2
    assert json.loads((out / "index" / "families.json").read_text()) == {"dw.orders": ["dw.orders_archive", "dw.stg_orders"]}
    seen = []
    for _ in range(2):
        r = json.loads(_json(capsys, out, "index", "next"))
        seen.append(r["packet"].get("copies"))
        ans = tmp_path / "a.json"
        ans.write_text(json.dumps({k: [] for k in ("relations", "columns", "tables", "concepts", "metrics", "performance")}))
        _json(capsys, out, "index", "submit", r["unit_id"], str(ans))
    withcopies = [c for c in seen if c]
    assert len(withcopies) == 1 and withcopies[0][0]["table"] == "dw.orders"
    assert withcopies[0][0]["copies"] == ["dw.orders_archive", "dw.stg_orders"]
