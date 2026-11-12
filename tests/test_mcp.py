"""The MCP server returns the same JSON as the CLI's --json outputs."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from keygraph import code  # noqa: E402
from keygraph import queries as QL  # noqa: E402
from keygraph.cli import main  # noqa: E402
from keygraph.mcp_server import build_server  # noqa: E402
from tests.codekit import CODE_FIX  # noqa: E402
from tests.test_example_shop import _build  # noqa: E402

TOOLS = {"search", "describe", "keys", "explain", "path", "neighbors", "areas", "area", "profile_summary", "code_where", "code_what",
         "query_for", "query_top", "query_search", "query_submit"}


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("mcp_shop")
    _build(d, profile=True)
    return d


@pytest.fixture(scope="module")
def srv(out):
    return build_server(out)


def call(srv, name, **args):
    """Return (is_error, parsed JSON or error text)."""
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        r = asyncio.run(srv.call_tool(name, args))
    except ToolError as exc:
        return True, str(exc)
    text = r.content[0].text
    return bool(r.is_error), (json.loads(text) if not r.is_error else text)


def cli_json(capsys, out, *argv):
    capsys.readouterr()
    assert main([*argv, "--dir", str(out), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_lists_tools_resources_and_instructions(srv):
    assert {t.name for t in asyncio.run(srv.list_tools())} == TOOLS
    assert all(len(t.description or "") > 80 for t in asyncio.run(srv.list_tools()))
    assert "read-only" in srv.instructions.lower() and "confidence" in srv.instructions
    assert "keygraph://areas/index" in {str(r.uri) for r in asyncio.run(srv.list_resources())}
    assert any("keygraph://tables/" in str(t.uri_template) for t in asyncio.run(srv.list_resource_templates()))


def test_path_matches_cli(srv, out, capsys):
    err, got = call(srv, "path", from_table="shop.customers", to_table="shop.shipments")
    assert not err and got == cli_json(capsys, out, "path", "shop.customers", "shop.shipments")
    assert "fan_out" in json.dumps(got)


def test_describe_has_grouped_relations(srv, out, capsys):
    err, got = call(srv, "describe", table="shop.orders")
    assert not err and got == cli_json(capsys, out, "describe", "shop.orders")
    assert got["relation_summary"]


def test_other_tools_match_cli(srv, out, capsys):
    pairs = [("keys", {"table": "shop.orders"}, ("keys", "shop.orders")),
             ("explain", {"column_ref": "shop.orders.customer_id"}, ("explain", "shop.orders.customer_id")),
             ("neighbors", {"table": "shop.orders", "depth": 2}, ("neighbors", "shop.orders", "--depth", "2")),
             ("search", {"query": "in transit"}, ("search", "in transit")),
             ("area", {"name_or_table": "Ordering"}, ("area", "Ordering"))]
    for tool, args, argv in pairs:
        err, got = call(srv, tool, **args)
        assert not err and got == cli_json(capsys, out, *argv), tool


def test_areas_and_mirrors(srv, out, capsys):
    err, got = call(srv, "areas")
    assert not err and got["areas"] == cli_json(capsys, out, "areas")
    err, more = call(srv, "areas", include_mirrors=True)
    assert more["areas"] == cli_json(capsys, out, "areas", "--include-mirrors")
    err, got = call(srv, "path", from_table="shop.customers", to_table="shop.shipments", include_mirrors=True)
    assert got == cli_json(capsys, out, "path", "shop.customers", "shop.shipments", "--include-mirrors")


def test_profile_summary_withholds_pii(srv):
    err, got = call(srv, "profile_summary", table="shop.customers")
    assert not err and got["table"] == "shop.customers"
    assert got["columns"]["email"]["pii"] is True
    assert "@example.com" not in json.dumps(got) and "top_values" not in got["columns"]["email"]
    err, none = call(srv, "profile_summary", table="shop.shipments")
    assert not err and none["columns"] == {}


def test_errors_are_tool_errors_with_suggestions(srv):
    err, msg = call(srv, "describe", table="customers")  # ambiguous: reporting.customers mirrors shop.customers
    assert err and "shop.customers" in msg
    err, msg = call(srv, "keys", table="custmers")
    assert err and "unknown table" in msg
    err, msg = call(srv, "explain", column_ref="shop.orders.nope")
    assert err


def test_table_resource(srv):
    out = asyncio.run(srv.read_resource("keygraph://tables/shop.orders"))
    assert "shop.orders" in list(out)[0].content
    assert "Ordering" in list(asyncio.run(srv.read_resource("keygraph://areas/index")))[0].content


def test_build_server_needs_a_graph(tmp_path):
    with pytest.raises(SystemExit):
        build_server(tmp_path)


def test_resource_path_traversal_is_an_error(srv):
    for uri in ("keygraph://tables/../../etc/passwd", "keygraph://tables//etc/passwd", "keygraph://tables/%2Fetc%2Fpasswd"):
        from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError
        with pytest.raises((ResourceError, ResourceNotFoundError)):
            asyncio.run(srv.read_resource(uri))


def test_serving_never_writes(tmp_path, out):
    import shutil
    d = tmp_path / "built"
    shutil.copytree(out, d)
    (d / "keygraph.db").unlink()
    before = {p.name: p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()}
    s = build_server(d)
    for tool, args in (("search", {"query": "orders"}), ("describe", {"table": "shop.orders"}),
                       ("area", {"name_or_table": "Ordering"}), ("profile_summary", {"table": "shop.orders"}),
                       ("code_where", {"table": "shop.orders"}), ("query_top", {}), ("query_for", {"table": "shop.orders"})):
        err, _ = call(s, tool, **args)
        assert not err, tool
    assert not (d / "keygraph.db").exists()
    assert {p.name: p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()} == before
    # with a db present it is opened read-only: contents unchanged
    shutil.copy(out / "keygraph.db", d / "keygraph.db")
    stamp = (d / "keygraph.db").read_bytes()
    call(s, "search", query="orders")
    assert (d / "keygraph.db").read_bytes() == stamp


def test_old_db_without_new_tables_reads_as_empty(tmp_path, out):
    """A keygraph.db from an older release (no curation, column_profile or table_info tables) must not crash."""
    import shutil
    import sqlite3
    d = tmp_path / "old"
    shutil.copytree(out, d)
    db = sqlite3.connect(d / "keygraph.db")
    for t in ("curation", "column_profile", "table_info", "column_details"):
        db.execute(f"DROP TABLE IF EXISTS {t}")
    db.commit()
    db.close()
    stamp = (d / "keygraph.db").read_bytes()
    s = build_server(d)
    for tool, args in (("search", {"query": "orders"}), ("describe", {"table": "shop.orders"}),
                       ("area", {"name_or_table": "Ordering"}), ("profile_summary", {"table": "shop.orders"}),
                       ("code_where", {"table": "shop.orders"}), ("query_top", {}), ("query_for", {"table": "shop.orders"})):
        err, msg = call(s, tool, **args)
        assert not err, (tool, msg)
    assert (d / "keygraph.db").read_bytes() == stamp


def test_failed_describe_closes_its_connection(out, monkeypatch):
    """Under the long-running server an unknown table must not leave a SQLite handle open."""
    from keygraph import cli, context
    opened, closed = [], []
    real_init = context.ContextDB.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        opened.append(self)
        real_db_close = self.db.close
        self.db = _Tracked(self.db, lambda: (closed.append(self), real_db_close()))

    monkeypatch.setattr(context.ContextDB, "__init__", init)
    g = cli.load_graph(out)
    for fn, arg in ((cli.describe_result, "nope_table"), (cli.area_result, "no such area")):
        with pytest.raises(ValueError):
            fn(out, g, arg, readonly=True) if fn is cli.describe_result else fn(out, g, arg, False, True)
    assert len(opened) >= 1 and len(closed) == len(opened)


class _Tracked:
    def __init__(self, db, on_close):
        self._db, self._on_close = db, on_close

    def close(self):
        self._on_close()

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_neighbors_depth_is_capped(srv):
    err, deep = call(srv, "neighbors", table="shop.orders", depth=50)
    assert not err and max(int(k) for k in deep["levels"]) <= 3


def test_code_tools_match_the_python_api(tmp_path, out):
    import shutil
    d = tmp_path / "built"
    shutil.copytree(out, d)
    code.index_repo(d / "keygraph.db", CODE_FIX / "php-doctrine", "php")
    s = build_server(d)
    err, got = call(s, "code_where", table="shop.orders", column="status")
    assert not err and got == code.where(d / "keygraph.db", "shop.orders.status", limit=50)
    assert [r["kind"] for r in got["refs"]] == ["declare_column", "use", "use"]
    err, got = call(s, "code_what", path="Order.php", line=19)
    assert not err and got["files"][0]["entity"]["table"] == "shop.orders"
    err, msg = call(s, "code_where", table="nosuch")
    assert err and "unknown table" in msg


def test_code_tools_without_an_index_are_empty(srv):
    err, got = call(srv, "code_where", table="shop.orders")
    assert not err and got["refs"] == [] and got["total"] == 0
    err, got = call(srv, "code_what", symbol="getStatus")
    assert not err and got == {"symbol": "getStatus", "refs": []}


def test_query_submit_then_read(tmp_path, out):
    import shutil
    d = tmp_path / "built"
    shutil.copytree(out, d)
    s = build_server(d)
    err, got = call(s, "query_submit", sql="SELECT id FROM shop.orders WHERE status = 3", source="sqlite:shop", ms=40,
                    context="paid orders", by="agent")
    assert not err and got["tables"] == ["shop.orders"]
    err, top = call(s, "query_top", table="shop.orders")
    assert not err and top == QL.top(d / "keygraph.db", table="shop.orders")
    assert top["queries"][0]["context"] == "paid orders"
    err, found = call(s, "query_for", table="shop.orders")
    assert not err and found["queries"][0]["fp"] == got["fp"]
    err, hits = call(s, "query_search", text="paid")
    assert not err and hits["queries"][0]["fp"] == got["fp"]
    err, msg = call(s, "query_submit", sql="", source="x")
    assert err and "empty SQL" in msg


def test_server_reloads_when_only_build_json_changes(tmp_path):
    import os

    d = tmp_path / "o"
    _build(d)
    s = build_server(d)
    err, msg = call(s, "keys", table="custmers")
    assert err and "built " in msg
    bj = d / "build.json"
    data = json.loads(bj.read_text())
    data["built_at"] = "2001-02-03T00:00:00Z"
    bj.write_text(json.dumps(data))
    st = bj.stat()
    os.utime(bj, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    err, msg = call(s, "keys", table="custmers")
    assert err and "built 2001-02-03" in msg


def test_describe_brief_matches_cli_and_is_smaller(srv, out, capsys):
    err, got = call(srv, "describe", table="shop.orders", brief=True)
    assert not err and got == cli_json(capsys, out, "describe", "shop.orders", "--brief")
    full = cli_json(capsys, out, "describe", "shop.orders")
    assert got["brief"] is True and "relations" not in got and "relation_summary" not in got
    assert set(got) >= {"table", "keys", "filter_columns", "info_line", "context", "columns"}
    assert all(set(c) <= {"name", "type", "key", "codes"} for c in got["columns"])
    assert [c["name"] for c in got["columns"]] == [c["name"] for c in full["columns"]]
    status = next(c for c in got["columns"] if c["name"] == "status")
    assert status["codes"]
    assert len(json.dumps(got)) < len(json.dumps(full)) / 2


def test_search_without_paths_matches_cli(srv, out, capsys):
    err, got = call(srv, "search", query="orders", with_paths=False)
    assert not err and got == cli_json(capsys, out, "search", "orders", "--no-paths")
    err, full = call(srv, "search", query="orders")
    assert not err and full == cli_json(capsys, out, "search", "orders") and full["join_paths"]
    assert got["join_paths"] == []
