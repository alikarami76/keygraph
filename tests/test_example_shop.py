"""The fictional webshop in examples/shop must keep showing what keygraph does."""

import json
from pathlib import Path

import pytest

from keygraph.cli import main

SHOP = Path(__file__).resolve().parents[1] / "examples" / "shop"


def _build(out: Path, profile: bool = False, app: bool = False) -> None:
    """Build the demo the way the README does. It reads context.yaml (extra yaml) and profiles through SQLAlchemy (extra live)."""
    pytest.importorskip("yaml", reason="needs PyYAML (extra: yaml)")
    if profile:
        pytest.importorskip("sqlalchemy", reason="needs SQLAlchemy (extra: live)")
    d = SHOP / "dumps"
    assert main(["load", "--dir", str(out), "--columns", str(d / "columns.json"), "--statistics", str(d / "statistics.json"),
                 "--key-columns", str(d / "key_columns.json"), "--tables", str(d / "tables.json"),
                 "--context", str(SHOP / "context.yaml")]) == 0
    if profile:
        assert main(["profile", "--dir", str(out), "--source", "sqlite", "--url", f"sqlite:///{SHOP / 'shop.db'}", "--bare-tables",
                     "--tables", "shop.customers,shop.orders,shop.order_items,shop.products,billing.payments"]) == 0
    if app:
        assert main(["code", "index", str(SHOP / "app"), "--name", "shop-app", "--dir", str(out)]) == 0
        assert main(["queries", "import", str(SHOP / "queries.jsonl"), "--dir", str(out)]) == 0
        assert main(["render", "--queries", "--dir", str(out)]) == 0


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("shop")
    _build(out, profile=True)
    return out


def test_areas_hub_and_mirror(built):
    g = json.loads((built / "graph.json").read_text())
    names = {a["name"] for a in g["areas"]}
    assert {"Ordering", "Catalog", "Fulfilment", "Payments and vouchers"} <= names
    assert "shop.users" in g["hubs"]
    assert g["mirrors"] == {"reporting": "shop"}
    assert len(g["nodes"]) >= 30 and len({n["schema"] for n in g["nodes"]}) == 3
    assert any(e["kind"] == "declared" for e in g["edges"]) and any(e["kind"] == "inferred" for e in g["edges"])


def test_path_has_fan_out_warning(built, capsys):
    capsys.readouterr()
    assert main(["path", "shop.customers", "shop.shipments", "--dir", str(built), "--json"]) == 0
    best = json.loads(capsys.readouterr().out)
    text = json.dumps(best)
    assert "shop.orders" in text and "fan_out" in text
    assert main(["path", "shop.customers", "shop.shipments", "--dir", str(built)]) == 0
    assert "fan-out" in capsys.readouterr().out


def test_profile_masks_email_and_flags_undocumented_status(built):
    g = json.loads((built / "graph.json").read_text())
    email = g["profiles"]["shop.customers.email"]
    assert email["pii"] is True and "top_values" not in email and "numeric" not in email
    assert "@example.com" not in json.dumps(g["profiles"])
    assert g["profiles"]["shop.orders.status"]["reconcile"]["undocumented_codes"] == ["9"]


def test_build_is_deterministic(tmp_path):
    _build(tmp_path / "a")
    _build(tmp_path / "b")
    assert (tmp_path / "a" / "graph.json").read_bytes() == (tmp_path / "b" / "graph.json").read_bytes()
    assert (tmp_path / "a" / "keygraph.html").read_bytes() == (tmp_path / "b" / "keygraph.html").read_bytes()


def test_search_status_label_finds_shipments_first(built, capsys):
    """README claim: a status label such as "in transit" finds the column that stores it (short words like
    "in" must not fragment-match inventory or invoices ahead of it)."""
    capsys.readouterr()
    assert main(["search", "in", "transit", "--dir", str(built), "--json"]) == 0
    tables = json.loads(capsys.readouterr().out)["tables"]
    assert tables[0]["table"] == "shop.shipments"
    assert "shop.inventory" not in [t["table"] for t in tables[:2]]


def test_explorer_search_status_label_finds_shipments_first(built):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no browser")
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto((built / "keygraph.html").as_uri() + "#search/in%20transit")
        pg.wait_for_selector("[data-view=search] .kg-result")
        top = pg.evaluate("window.__kg.searchDetail('in transit')")[0]
        b.close()
    assert top["kind"] == "value" and top["label"].startswith("shop.shipments.status"), top
    assert not errors


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("shop_app")
    _build(out, app=True)
    return out


def test_code_index_links_the_example_app(demo, capsys):
    capsys.readouterr()
    assert main(["code", "where", "shop.orders", "--dir", str(demo), "--json"]) == 0
    refs = {(r["kind"], r["path"], r["line"], r["column"]) for r in json.loads(capsys.readouterr().out)["refs"]}
    assert ("declare_table", "shop/models.py", 17, None) in refs
    assert ("declare_column", "shop/models.py", 20, "status") in refs
    assert ("sql_write", "shop/orders.py", 17, None) in refs
    assert main(["code", "where", "billing.refunds", "--kind", "sql_write", "--dir", str(demo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["refs"][0]["path"] == "sql/reports.sql"
    assert main(["code", "where", "shop.shipments.tracking_no", "--dir", str(demo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["refs"][0]["path"] == "web/shipment.ts"
    page = (demo / "tables" / "shop.orders.md").read_text()
    assert "## Used in code" in page and "## Queries" in page


def test_query_log_of_the_example(demo, capsys):
    capsys.readouterr()
    assert main(["queries", "stats", "--dir", str(demo), "--json"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert (s["runs"], s["fingerprints"], s["failed_runs"]) == (7, 5, 1)
    assert s["unresolved"] == [{"name": "shop.order_archive", "runs": 1}]
    assert main(["queries", "top", "--table", "shop.orders", "--dir", str(demo), "--json"]) == 0
    top = json.loads(capsys.readouterr().out)["queries"][0]
    assert (top["runs"], top["people"], top["context"]) == (3, 2, "Paid orders this month")
    assert main(["queries", "slow", "--limit", "1", "--dir", str(demo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["queries"][0]["context"] == "Refunded payments this month"


def test_demo_with_app_is_deterministic(tmp_path):
    _build(tmp_path / "a", app=True)
    _build(tmp_path / "b", app=True)
    for f in ("graph.json", "keygraph.html", "tables/shop.orders.md"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes(), f


def _tree(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file() and p.suffix != ".db"}


def test_build_json_is_the_only_file_that_differs_between_builds(tmp_path, monkeypatch):
    import keygraph

    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    a, b = tmp_path / "a", tmp_path / "b"
    _build(a)
    _build(b)
    ta, tb = _tree(a), _tree(b)
    assert ta.keys() == tb.keys() and "build.json" in ta
    assert [k for k in ta if ta[k] != tb[k] and k != "build.json"] == []
    meta = json.loads(ta["build.json"])
    assert meta["keygraph_version"] == keygraph.__version__
    assert meta["schemas"] == sorted({n["schema"] for n in json.loads(ta["graph.json"])["nodes"]})
    assert meta["sources"] == [{"kind": "dump", "files": ["columns.json", "statistics.json", "key_columns.json", "tables.json"]}]
    assert "built_at" in meta and "built_at" not in ta["graph.json"].decode()
    assert str(tmp_path) not in ta["build.json"].decode()


def test_build_json_honours_source_date_epoch_and_survives_render(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    _build(tmp_path)
    first = (tmp_path / "build.json").read_text()
    assert json.loads(first)["built_at"] == "2023-11-14T22:13:20Z"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1800000000")
    assert main(["render", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "build.json").read_text() == first


TRICKY_URLS = [
    ("mysql://u:pa/ss@hostx/shop", "shop"),
    ("mysql://u:pa/ss@hostx", None),
    ("mysql://u:pw/@hostx", None),
    ("mysql://us3r:p@ss:w@hostx:3307/shop", "shop"),
    ("mysql+pymysql://us3r:pw@hostx/shop?ssl=1&unix_socket=/tmp/hostx.sock", "shop"),
    ("mssql+pyodbc:///?odbc_connect=DRIVER%3DX%3BSERVER%3Dhostx%3BPWD%3Dpw", None),
    ("postgresql://us3r:pw@hostx:5432/", None),
    ("postgresql://us3r:pw@hostx:5432/we!rd@db", None),
    ("mysql://us3r:pw@hostx/\u0645\u062a\u0631\u0648", "\u0645\u062a\u0631\u0648"),
]


@pytest.mark.parametrize("url,db", TRICKY_URLS)
def test_scan_source_description_has_no_credentials(url, db):
    from keygraph.cli import url_source
    src = url_source(url)
    assert src.get("database") == db
    for frag in ("hostx", "us3r", "pw", "3307", "5432", "ssl", "sock", "pa/ss", "p@ss"):
        assert frag not in json.dumps(src), (url, frag)
    assert url_source("sqlite:////tmp/x/shop.db") == {"kind": "sqlite", "database": "shop.db"}


def test_bad_source_date_epoch_is_ignored_with_a_warning(tmp_path, monkeypatch, capsys):
    for bad in ("\u00b2", "9" * 30, "abc", "-5"):
        monkeypatch.setenv("SOURCE_DATE_EPOCH", bad)
        d = tmp_path / ("o" + str(abs(hash(bad))))
        _build(d)
        assert (d / "build.json").exists()
        assert "SOURCE_DATE_EPOCH" in capsys.readouterr().err


def test_build_json_is_written_before_graph_json(tmp_path, monkeypatch):
    from keygraph import cli
    order = []
    real = cli.write_build_info
    monkeypatch.setattr(cli, "write_build_info", lambda *a, **k: (order.append("build"), real(*a, **k)))
    monkeypatch.setattr(cli.Graph, "save", lambda self, p: order.append("graph"))
    try:
        _build(tmp_path)
    except Exception:  # noqa: BLE001  (graph.json is not really saved here)
        pass
    assert order[:2] == ["build", "graph"]


def test_unknown_table_names_coverage_cli_and_mcp_alike(built, capsys):
    assert main(["describe", "custmers", "--dir", str(built)]) == 2
    cli = capsys.readouterr().err.strip().removeprefix("keygraph: ")
    assert cli.startswith("unknown table 'custmers'")
    assert "this map covers schemas" in cli and "built " in cli and "from dump" in cli and "--all-schemas" in cli
    pytest.importorskip("mcp")
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError

    from keygraph.mcp_server import build_server
    with pytest.raises(ToolError) as exc:
        asyncio.run(build_server(built).call_tool("describe", {"table": "custmers"}))
    assert str(exc.value).removeprefix("Error executing tool describe: ") == cli
