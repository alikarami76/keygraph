"""Dev mode on the MCP server: calls and resources are logged when on (checked live), nothing is written when off."""

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from keygraph import devlog  # noqa: E402
from keygraph import search as S  # noqa: E402
from keygraph.cli import main  # noqa: E402
from keygraph.mcp_server import _client, build_server  # noqa: E402
from tests.test_example_shop import _build  # noqa: E402
from tests.test_mcp import TOOLS, call  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("KEYGRAPH_DEV", raising=False)
    monkeypatch.delenv("KEYGRAPH_DEV_SESSION", raising=False)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("devmcp_shop")
    _build(d, profile=True)
    return d


@pytest.fixture
def out(built, tmp_path):
    d = tmp_path / "o"
    shutil.copytree(built, d)
    return d


def events(out):
    p = out / "dev" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def names(srv):
    return {t.name for t in asyncio.run(srv.list_tools())}


def dev_on(out):
    assert main(["dev", "on", "--dir", str(out)]) == 0


def test_off_lists_15_tools_and_writes_nothing(out):
    srv = build_server(out)
    assert names(srv) == TOOLS and len(TOOLS) == 15
    before = {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()}
    for tool, args in (("search", {"query": "orders"}), ("describe", {"table": "shop.orders"}),
                       ("profile_summary", {"table": "shop.orders"}), ("query_top", {})):
        assert not call(srv, tool, **args)[0]
    asyncio.run(srv.read_resource("keygraph://areas/index"))
    assert not (out / "dev").exists()
    assert {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file() and "keygraph.db" not in p.name} == \
        {p: t for p, t in before.items() if "keygraph.db" not in p.name}


def test_dev_on_is_picked_up_live_and_events_have_fields(out):
    srv = build_server(out)  # started with dev off
    call(srv, "search", query="orders")
    assert not events(out)
    dev_on(out)
    assert names(srv) == TOOLS  # dev_note is only registered when dev is on as the server starts
    err, got = call(srv, "search", query="orders", limit=3)
    assert not err
    (ev,) = events(out)
    assert (ev["surface"], ev["kind"], ev["tool"], ev["ok"]) == ("mcp", "call", "search", True)
    assert ev["args"] == {"query": "orders", "limit": 3} and ev["ms"] >= 0 and ev["session"] == devlog.session_id("mcp")
    assert ev["response"] == got and ev["client"] == {}
    call(srv, "keys", table="shop.orders")
    assert [e["seq"] for e in events(out)] == [1, 2] and len({e["session"] for e in events(out)}) == 1


def test_errors_are_recorded_and_reraised(out):
    dev_on(out)
    srv = build_server(out)
    err, msg = call(srv, "describe", table="no_such_table")
    assert err
    (ev,) = events(out)
    assert ev["ok"] is False and ev["error"]["type"] == "ToolError" and ev["error"]["message"].startswith("Error executing tool describe")
    assert "response" not in ev and "response_ref" not in ev


def test_client_info_is_read_defensively(out, monkeypatch):
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        client_info=SimpleNamespace(name="claude-code", version="9.9"))))
    assert _client(ctx) == {"name": "claude-code", "version": "9.9"}
    for bad in (None, object(), SimpleNamespace(session=None), SimpleNamespace(session=SimpleNamespace(client_params=None))):
        assert _client(bad) == {}
    monkeypatch.setattr("keygraph.mcp_server._client", lambda c: {"name": "x", "version": "1"})
    dev_on(out)
    call(build_server(out), "keys", table="shop.orders")
    assert events(out)[0]["client"] == {"name": "x", "version": "1"}


def test_resource_reads_are_logged(out, monkeypatch):
    dev_on(out)
    srv = build_server(out)
    asyncio.run(srv.read_resource("keygraph://areas/index"))
    asyncio.run(srv.read_resource("keygraph://tables/shop.orders"))
    a, t = events(out)
    assert a["kind"] == "resource" and a["args"] == {"uri": "keygraph://areas/index"} and a["ok"]
    assert "Ordering" in a["response"] and t["tool"] == "read_resource"
    monkeypatch.setattr(devlog, "INLINE_MAX", 50)
    asyncio.run(srv.read_resource("keygraph://areas/index"))
    big = events(out)[-1]
    assert "response" not in big and big["response_ref"]["bytes"] > 50 and len(big["response_ref"]["sha256"]) == 64
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(srv.read_resource("keygraph://tables/nope_nope"))
    assert events(out)[-1]["ok"] is False


def test_decisions_are_collected_per_call(out, monkeypatch):
    dev_on(out)
    real = S.search

    def fake(*a, **k):
        devlog.decide("fake", why="because")
        return real(*a, **k)
    monkeypatch.setattr(S, "search", fake)
    srv = build_server(out)
    call(srv, "search", query="orders")
    call(srv, "keys", table="shop.orders")
    first, second = events(out)
    assert first["decisions"] == [{"kind": "fake", "why": "because"}] and "decisions" not in second


def test_masking_in_args_and_responses(out):
    dev_on(out)
    srv = build_server(out)
    sql = "select * from shop.customers where email = 'bob@example.com' and name = 'Bobby' and password=hunter2"
    assert not call(srv, "query_submit", sql=sql, source="t", context="why password=hunter2 for 09123456789")[0]
    call(srv, "search", query="find 09123456789 or ann@example.org")
    raw = (out / "dev" / "events.jsonl").read_text()
    for leak in ("bob@example.com", "Bobby", "hunter2", "09123456789", "ann@example.org"):
        assert leak not in raw
    sub = events(out)[0]
    assert "?" in sub["args"]["sql"]
    err, prof = call(srv, "profile_summary", table="shop.orders")
    err2, desc = call(srv, "describe", table="shop.orders")
    evs = {e["tool"]: e for e in events(out)}
    assert "<masked>" in json.dumps(evs["profile_summary"]["response"])
    assert "<masked>" in json.dumps(evs["describe"]["response"]) or "top_values" not in json.dumps(desc)
    for v in json.dumps(prof).split('"top_values": ')[1:]:
        assert v[:5] != "[{\"v"
    assert not err and not err2


def test_big_response_becomes_a_ref(out, monkeypatch):
    dev_on(out)
    monkeypatch.setattr(devlog, "INLINE_MAX", 300)
    srv = build_server(out)
    err, got = call(srv, "neighbors", table="shop.orders", depth=3)
    assert not err
    (ev,) = events(out)
    ref = ev["response_ref"]
    assert "response" not in ev and ref["bytes"] > 300 and ref["tokens_approx"] > 0 and "keys" in ref["shape"]


def test_a_failing_recorder_leaves_the_result_unchanged(out, monkeypatch, capsys):
    srv = build_server(out)
    _, want = call(srv, "search", query="orders")
    dev_on(out)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(devlog.Recorder, "record", boom)
    assert call(srv, "search", query="orders") == (False, want)
    asyncio.run(srv.read_resource("keygraph://areas/index"))
    assert not events(out)


def test_dev_note_only_when_on_and_matches_cli(out, capsys):
    dev_on(out)
    srv = build_server(out)
    assert names(srv) == TOOLS | {"dev_note"}
    tool = next(t for t in asyncio.run(srv.list_tools()) if t.name == "dev_note")
    assert len(tool.description) > 80
    err, got = call(srv, "dev_note", question="how do orders reach shipments? password=hunter2")
    assert not err and got["noted"] is True and "hunter2" not in got["question"]
    import os
    os.environ["KEYGRAPH_DEV_SESSION"] = got["session"]
    capsys.readouterr()
    assert main(["dev", "note", "how do orders reach shipments? password=hunter2", "--dir", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == got
    notes = [e for e in events(out) if e["kind"] == "note"]
    assert len(notes) == 2 and notes[0]["surface"] == "mcp" and "dev_note" not in [e["tool"] for e in events(out) if e["kind"] == "call"]
    call(srv, "search", query="orders")
    assert {e["session"] for e in events(out)} == {got["session"]}
    # the tool answers while dev is off again, and records nothing
    main(["dev", "off", "--dir", str(out)])
    n = len(events(out))
    assert call(srv, "dev_note", question="x")[1]["noted"] is False and len(events(out)) == n


def test_doctor_accepts_dev_note(out):
    from keygraph import doctor
    dev_on(out)
    r = doctor.run(out)
    mcp = next(c for c in r["checks"] if c["name"] == "mcp")
    assert mcp["status"] == "ok" and "16 tools" in mcp["detail"]


def test_parity_with_dev_on(out, capsys):
    dev_on(out)
    srv = build_server(out)
    for tool, args, argv in (("path", {"from_table": "shop.customers", "to_table": "shop.shipments"},
                              ["path", "shop.customers", "shop.shipments"]),
                             ("describe", {"table": "shop.orders"}, ["describe", "shop.orders"]),
                             ("keys", {"table": "shop.orders"}, ["keys", "shop.orders"]),
                             ("neighbors", {"table": "shop.orders"}, ["neighbors", "shop.orders"])):
        _, got = call(srv, tool, **args)
        capsys.readouterr()
        assert main([*argv, "--dir", str(out), "--json"]) == 0
        assert got == json.loads(capsys.readouterr().out), tool
