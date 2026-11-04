""""Used in code" in keygraph.html and the table pages; snippets only with --snippets."""
import json

import pytest

from keygraph.cli import main
from keygraph.explorer import compact
from keygraph.graph import Graph
from tests.codekit import CODE_FIX, build_graph_dir


@pytest.fixture()
def out(tmp_path):
    build_graph_dir(tmp_path)
    assert main(["code", "index", str(CODE_FIX / "php-doctrine"), "--name", "php", "--dir", str(tmp_path)]) == 0
    return tmp_path


def summary(out, snippets=False):
    from keygraph.code.store import CodeStore
    with CodeStore(out / "keygraph.db", readonly=True) as st:
        return st.summary(snippets=snippets)


def test_summary_caps_and_orders_declarations_first(out):
    s = summary(out)
    t = s["app.orders"]
    assert t["n"] == {"declare_table": 1, "declare_column": 3, "sql_write": 0, "sql_read": 0, "use": 3}
    assert [r["kind"] for r in t["refs"]][:4] == ["declare_table", "declare_column", "declare_column", "declare_column"]
    assert "snippet" not in t["refs"][0] and "snippet" in summary(out, snippets=True)["app.orders"]["refs"][0]
    assert t["columns"]["status"]["n"]["use"] == 2


def test_compact_payload(out):
    g = Graph.load(out / "graph.json")
    data = compact(g, None, code=summary(out))
    ids = sorted(g.nodes)
    k = data["k"]
    assert k["r"] == ["php"]
    counts, refs, cols = k["t"][str(ids.index("app.orders"))]
    assert counts == [1, 3, 0, 0, 3] and refs[0] == [0, "Order.php", 8, 0, 1.0, -1]
    status = [c["name"] for c in g.nodes["app.orders"]["columns"]].index("status")
    assert cols[str(status)][0] == [0, 1, 0, 0, 2]
    assert "k" not in compact(g, None)


def test_table_page_lists_declarations(out):
    page = (out / "tables" / "app.orders.md").read_text()
    assert "## Used in code" in page and "7 refs (declare_table 1, declare_column 3, use 3)." in page
    assert "- `php:Order.php:19` status" in page
    assert "## Used in code" not in (out / "tables" / "app.payments.md").read_text()


def test_snippets_flag_is_remembered(out):
    html = (out / "keygraph.html").read_text()
    assert "private string $status;" not in html
    assert main(["render", "--snippets", "--dir", str(out)]) == 0
    assert json.loads((out / "graph.json").read_text())["render_options"]["snippets"] is True
    assert main(["render", "--dir", str(out)]) == 0  # a later render keeps the choice
    assert "private string $status;" in (out / "keygraph.html").read_text()


def test_explorer_shows_used_in_code(out):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    g = json.loads((out / "graph.json").read_text())
    area = next(a["id"] for a in g["areas"] if "app.orders" in a["tables"])
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no browser")
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        url = (out / "keygraph.html").as_uri()
        pg.goto(url + "#table/app.orders")
        pg.wait_for_selector("[data-view=table] [data-code]")
        assert "Used in code" in pg.text_content("[data-view=table]")
        assert pg.locator("[data-view=table] [data-coderef]").first.inner_text().startswith("php:Order.php:8")
        assert pg.locator("[data-snippet]").count() == 0
        pg.goto(url + "#col/app.orders/status")
        pg.wait_for_selector("[data-view=column] [data-code]")
        assert "php:Order.php:19" in pg.text_content("[data-view=column]")
        pg.goto(url + "#area/" + area)
        pg.wait_for_selector("[data-incode]")
        assert sorted(pg.locator("[data-incode]").all_text_contents()) == ["3 in code", "7 in code"]
        b.close()
    assert not errors
