""""Queries" in keygraph.html and the table pages, only with --queries."""
import json

import pytest

from keygraph import queries as QL
from keygraph.cli import main
from keygraph.explorer import compact
from keygraph.graph import Graph
from tests.codekit import build_graph_dir


@pytest.fixture()
def out(tmp_path):
    db = build_graph_dir(tmp_path)
    QL.add(db, "SELECT id FROM orders WHERE status = 1", "pg", ms=12, context="paid orders", at="2026-11-01T00:00:00")
    QL.add(db, "SELECT id FROM orders WHERE status = 2", "pg", ms=18, at="2026-11-02T00:00:00")
    return tmp_path


def test_only_with_queries_flag(out):
    assert main(["render", "--dir", str(out)]) == 0
    assert "## Queries" not in (out / "tables" / "app.orders.md").read_text()
    assert '"q":' not in (out / "keygraph.html").read_text()
    assert main(["render", "--queries", "--dir", str(out)]) == 0
    page = (out / "tables" / "app.orders.md").read_text()
    assert "## Queries" in page and "2 runs, median 15 ms, p95 18 ms: paid orders" in page
    assert "select id from orders where status = ?" in page
    assert json.loads((out / "graph.json").read_text())["render_options"]["queries"] is True


def test_compact_payload(out):
    g = Graph.load(out / "graph.json")
    rows = QL.summary(out / "keygraph.db")
    data = compact(g, None, queries=rows)
    q = data["q"][str(sorted(g.nodes).index("app.orders"))]
    assert q[0]["runs"] == 2 and q[0]["sql"] == "select id from orders where status = ?" and q[0]["context"] == "paid orders"
    assert "q" not in compact(g, None)


def test_explorer_shows_queries(out):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    assert main(["render", "--queries", "--dir", str(out)]) == 0
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no browser")
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto((out / "keygraph.html").as_uri() + "#table/app.orders")
        pg.wait_for_selector("[data-view=table] [data-queries]")
        text = pg.text_content("[data-view=table] [data-queries]")
        b.close()
    assert "paid orders" in text and "2 runs" in text and "median 15 ms" in text
    assert not errors
