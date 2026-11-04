"""Browser tests for keygraph.html (Playwright; skipped when no browser is installed) plus output privacy checks."""
import json
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture()
def page(tmp_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("no browser")
        pg = b.new_page(viewport={"width": 1280, "height": 800})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        yield pg
        b.close()


def build(tmp_path, schema, context=None):
    from keygraph.cli import run_pipeline
    run_pipeline(schema, tmp_path, context=context, verbose=False)
    return (tmp_path / "keygraph.html").as_uri()


def test_hub_star_never_draws_more_than_40(page, tmp_path):
    from tests.test_hubs import star
    page.goto(build(tmp_path, star(500)))
    page.wait_for_selector("[data-view=areas]")
    page.goto(page.url.split("#")[0] + "#table/app.users")
    page.wait_for_selector("[data-view=table]")
    assert page.locator("[data-group-collapsed]").count() >= 1
    assert "500" in page.inner_text("[data-view=table]")
    page.click("text=Open area")
    page.wait_for_selector("[data-view=area]")
    assert page.evaluate("window.__kg.maxNodesDrawn") <= 40


def test_views_render(page, tmp_path, dump_schema):
    page.goto(build(tmp_path, dump_schema))
    page.wait_for_selector("[data-view=areas]")
    assert page.locator("[data-area]").count() >= 1
    page.fill("[data-search]", "orders")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-view=table]")
    assert "shop.orders" in page.inner_text("[data-view=table] h1")
    page.click("text=Path from here")
    page.fill("[data-path-to]", "products")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-step]")
    assert page.locator("[data-step]").count() >= 1
    assert page.evaluate("window.__kg.maxNodesDrawn") <= 40


def test_js_path_ranking_matches_python(page, tmp_path, dump_schema):
    from keygraph.graph import Graph
    from keygraph.query import find_paths
    page.goto(build(tmp_path, dump_schema))
    page.wait_for_selector("[data-view=areas]")
    g = Graph.load(tmp_path / "graph.json")
    for a, b in (("shop.customers", "shop.products"), ("shop.products", "shop.customers"), ("logs.access_log", "shop.sellers")):
        py = find_paths(g, a, b)
        js = page.evaluate("([a, b]) => window.__kg.findPaths(a, b)", [a, b])
        assert [p["tables"] for p in js] == [p["tables"] for p in py], (a, b)
        assert [p["score"] for p in js] == [p["score"] for p in py], (a, b)
        assert [[s["warnings"] for s in p["steps"]] for p in js] == [[s["warnings"] for s in p["steps"]] for p in py]


def test_path_shows_fan_out_and_sql(page, tmp_path, dump_schema):
    page.goto(build(tmp_path, dump_schema) + "#path/shop.customers/shop.products")
    page.wait_for_selector("[data-step]")
    text = page.inner_text("[data-view=path]")
    assert "fan-out" in text and "JOIN shop.orders" in text
    assert page.locator(".kg-chain-card").count() == 4
    page.click("text=Copy SQL")  # clipboard may be denied; must not throw
    assert not page.errors


def test_every_route_renders_in_both_themes(page, tmp_path, dump_schema):
    url = build(tmp_path, dump_schema, context=[str(Path(__file__).parent / "fixtures" / "context.yaml")])
    for theme in ("light", "dark"):
        page.emulate_media(color_scheme=theme)
        for route, view in (("#areas", "areas"), ("#area/" + area_id(tmp_path, "shop.orders"), "area"),
                            ("#table/shop.orders", "table"), ("#col/shop.orders/status", "column"),
                            ("#path/shop.customers/shop.products", "path"), ("#search/paid", "search")):
            page.goto(url + route)
            page.wait_for_selector(f"[data-view={view}]")
            assert page.evaluate("window.__kg.maxNodesDrawn") <= 40
    assert not page.errors, page.errors


def test_search_on_enum_label_lands_on_column(page, tmp_path, dump_schema):
    url = build(tmp_path, dump_schema, context=[str(Path(__file__).parent / "fixtures" / "context.yaml")])
    page.goto(url + "#search/shipped")
    page.wait_for_selector("[data-view=search] .kg-result")
    assert "enum label" in page.inner_text("[data-view=search]")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-view=column]")
    assert "status" in page.inner_text("[data-view=column] h1")


def test_back_and_forward(page, tmp_path, dump_schema):
    url = build(tmp_path, dump_schema)
    page.goto(url + "#table/shop.orders")
    page.wait_for_selector("[data-view=table]")
    page.goto(url + "#table/shop.products")
    page.wait_for_selector("[data-view=table] h1:has-text('products')")
    page.go_back()
    page.wait_for_selector("[data-view=table] h1:has-text('orders')")
    page.go_forward()
    page.wait_for_selector("[data-view=table] h1:has-text('products')")


def test_damaged_data_shows_error(page, tmp_path, dump_schema):
    build(tmp_path, dump_schema)
    html = (tmp_path / "keygraph.html").read_text(encoding="utf-8")
    start = html.index('id="kg-data">') + len('id="kg-data">')
    bad = tmp_path / "bad.html"
    bad.write_text(html[:start] + "{broken" + html[html.index("</script>", start):], encoding="utf-8")
    page.goto(bad.as_uri())
    page.wait_for_selector(".kg-error")
    assert "keygraph build" in page.inner_text(".kg-error")


def area_id(out, table):
    g = json.loads((out / "graph.json").read_text())
    return next(a["id"] for a in g["areas"] if table in a["tables"])


def test_markup_in_context_renders_as_text(page, tmp_path, dump_schema):
    nasty = "watch out <!-- not a comment --> and </script><b>bold</b>"
    f = tmp_path / "c.yaml"
    f.write_text("tables:\n  - table: shop.orders\n    summary: '" + nasty + "'\n", encoding="utf-8")
    out = tmp_path / "out"
    page.goto(build(out, dump_schema, context=[str(f)]) + "#table/shop.orders")
    page.wait_for_selector("[data-view=table]")
    assert nasty in page.inner_text("[data-view=table]")
    assert not page.errors


def many_areas(n_groups=45):
    """n_groups small connected areas, plus loose tables in two schemas (two Unconnected areas)."""
    from keygraph.model import Column, Index, Schema, Table
    pk = [Index("PRIMARY", ["id"], True, True)]
    s = Schema()
    for g in range(n_groups):
        s.add(Table("app", f"g{g}_root", [Column("id", "int")], list(pk)))
        for part in ("item", "note"):
            s.add(Table("app", f"g{g}_{part}", [Column("id", "int"), Column(f"g{g}_root_id", "int")], list(pk)))
    for sch in ("logs", "arch"):
        for i in range(4):
            s.add(Table(sch, f"{sch}_loose{i}", [Column("id", "int"), Column("payload", "text")], list(pk)))
    return s


def test_map_folds_unconnected_areas_outside_the_budget(page, tmp_path):
    page.goto(build(tmp_path, many_areas()))
    page.wait_for_selector("[data-view=areas] [data-area]")
    m = page.evaluate("window.__kg.map")
    assert m["notConnected"] == 2 and m["boxes"] == page.evaluate("window.__kg.maxNodesDrawn") <= 40
    assert page.locator("[data-area=loose][data-not-connected]").count() == 1
    assert page.locator("[data-area=other]").count() == 1          # 45 connected areas: as many as fit, then Other
    assert m["connectedShown"] + m["otherAreas"] == 45 and m["connectedShown"] <= 38
    assert "Unconnected" not in page.inner_text("[data-view=areas] h1 ~ *, .kg-panel details:has(summary:text('Largest connected areas'))")
    assert not page.locator("[data-area] .kg-area-name:text-matches('^Unconnected')").count()
    assert not page.errors


def test_path_endpoints_never_get_the_hub_warning(page, tmp_path):
    from keygraph.graph import Graph
    from keygraph.query import find_paths
    from tests.test_query import hub_star
    g0 = hub_star()
    schema = __import__("keygraph.model", fromlist=["Schema"]).Schema()
    from keygraph.model import Column, Index, Table
    for n in g0.nodes.values():
        schema.add(Table(n["schema"], n["table"], [Column(c["name"], c["type"]) for c in n["columns"]],
                         [Index(i["name"], i["columns"], i["unique"], i["primary"]) for i in n["indexes"]]))
    pk = [Index("PRIMARY", ["id"], True, True)]  # a scratch table on one route, so the log penalty is cross-checked too
    schema.add(Table("app", "orders_shadow", [Column("id", "int"), Column("order_id", "int")], list(pk)))
    schema.add(Table("app", "invoices", [Column("id", "int"), Column("orders_shadow_id", "int"), Column("user_id", "int")], list(pk)))
    url = build(tmp_path, schema)
    g = Graph.load(tmp_path / "graph.json")
    page.goto(url)
    page.wait_for_selector("[data-view=areas]")
    assert any("log" in s["warnings"] for p in find_paths(g, "app.orders", "app.invoices", 4, 0.4, 3) for s in p["steps"])
    for a, b in (("app.orders", "app.users"), ("app.users", "app.orders"), ("app.orders", "app.addresses"), ("app.x1", "app.x2"),
                 ("app.orders", "app.invoices"), ("app.invoices", "app.addresses")):
        py = find_paths(g, a, b, 4, 0.4, 3)
        js = page.evaluate("([a, b]) => window.__kg.findPaths(a, b, 4, 0.4, 3)", [a, b])
        assert [(p["tables"], p["score"], [s["warnings"] for s in p["steps"]]) for p in js] == \
               [(p["tables"], p["score"], [s["warnings"] for s in p["steps"]]) for p in py], (a, b)
    page.goto(url + "#path/app.orders/app.users")
    page.wait_for_selector("[data-step]")
    assert "hub" not in page.inner_text(".kg-route-head") and "hub:" not in page.inner_text(".kg-panel")
    assert "score" in page.inner_text("[data-view=path]")
    page.goto(url + "#path/app.orders/app.addresses")
    page.wait_for_selector("[data-step]")
    assert "1 hub step" in page.inner_text("[data-view=path]") and "hub: users" in page.inner_text(".kg-panel")


def test_mirror_toggle_and_js_python_agree(page, tmp_path):
    from keygraph.graph import Graph
    from keygraph.query import find_paths
    from tests.test_mirrors import twins
    url = build(tmp_path, twins())
    g = Graph.load(tmp_path / "graph.json")
    page.goto(url)
    page.wait_for_selector("[data-view=areas]")
    for inc in (False, True):
        py = [p["tables"] for p in find_paths(g, "shop.events", "shop.legacy", 4, 0.4, 3, include_mirrors=inc)]
        js = [p["tables"] for p in page.evaluate("(inc) => window.__kg.findPaths('shop.events', 'shop.legacy', 4, 0.4, 3, inc)", inc)]
        assert js == py
    assert page.locator("[data-area]").count() == len([a for a in g.data["areas"] if not a.get("mirror")])
    page.goto(url + "#path/shop.events/shop.legacy")
    page.wait_for_selector(".kg-empty")
    page.click("[data-act=settings]")  # at 1280 px the rail is collapsed; its toggles live in a popover
    page.locator(".kg-popover [data-act=mirrors]").check()
    page.wait_for_selector("[data-step]")
    assert "shop_copy" in page.inner_text("[data-view=path]") or "orders" in page.inner_text("[data-view=path]")


def test_markup_and_placeholders(page, tmp_path, dump_schema):
    f = tmp_path / "c.yaml"
    f.write_text("""tables:
  - table: shop.orders
    grain: "_Not recorded yet._"
    summary: "Use `status` with **care** | never join on `code`"
    pitfalls: "rows with `status = 1` are carts | **seller 1** is the shop itself"
""", encoding="utf-8")
    page.goto(build(tmp_path / "o", dump_schema, context=[str(f)]) + "#table/shop.orders")
    page.wait_for_selector("[data-view=table]")
    view = page.locator("[data-view=table]")
    assert view.locator("code:text('status')").count() >= 1 and view.locator("strong:text('care')").count() == 1
    assert view.locator(".kg-callout--pitfall li").count() == 2
    text = view.inner_text()
    assert "Not recorded yet" not in text and "**" not in text and "`" not in text and " | " not in text


def test_exact_enum_label_ranks_above_fragments_and_highlights_whole_words(page, tmp_path):
    from keygraph.model import Column, Index, Schema, Table
    pk = [Index("PRIMARY", ["id"], True, True)]
    s = Schema()
    s.add(Table("app", "moderation", [Column("id", "int"), Column("status", "int")], list(pk)))
    for n in ("in_stock_reviews", "review_index", "pricing_in_reviews", "inbox_review"):
        s.add(Table("app", n, [Column("id", "int")], list(pk)))
    f = tmp_path / "c.yaml"
    f.write_text("columns:\n  - {table: app.moderation, column: status, codes: {'1': in_review, '2': approved}}\n"
                 "concepts:\n  - {name: in review workflow, definition: how items move through moderation}\n", encoding="utf-8")
    page.goto(build(tmp_path / "o", s, context=[str(f)]) + "#search/in_review")
    page.wait_for_selector("[data-view=search] .kg-result")
    hits = page.evaluate("window.__kg.searchDetail('in_review')")
    top = hits[0]
    assert top["kind"] == "value" and top["label"].endswith("= 1")
    kinds = [h["kind"] for h in hits]
    assert "concept" in kinds and kinds.index("concept") > kinds.index("value")  # a phrase in a concept name ranks lower
    assert page.locator(".kg-result-group h3").first.inner_text().lower() == "values"
    html = page.inner_html("#sres")
    import re
    assert not re.search(r"[A-Za-z0-9]<mark>", html), "a highlight starts inside a word"


def big_area(n_children=40):
    """One area of 41 tables (a root and children pointing to it) beside the many-areas graph."""
    from keygraph.model import Column, Index, Table
    s = many_areas()
    pk = [Index("PRIMARY", ["id"], True, True)]
    s.add(Table("hq", "accounts", [Column("id", "int")], list(pk)))
    for i in range(n_children):
        s.add(Table("hq", f"account_part{i}", [Column("id", "int"), Column("account_id", "int")], list(pk)))
    return s


def test_first_paint_respects_the_minimum_zoom(page, tmp_path):
    url = build(tmp_path, big_area())
    g = json.loads((tmp_path / "graph.json").read_text())
    aid = next(a["id"] for a in g["areas"] if "hq.accounts" in a["tables"])
    page.goto(url + "#areas")
    page.wait_for_selector("[data-view=areas] [data-area]")
    assert page.evaluate("window.__kg.zoom") >= 0.7
    assert page.evaluate("window.__kg.maxNodesDrawn") <= 40 and page.locator("[data-area=other]").count() == 1
    assert page.locator("[data-area=loose]").count() == 1
    page.click("[data-area=other]")                                  # Other areas opens its list in the panel
    assert page.locator("#otherlist[open] .kg-level").count() >= 1
    page.goto(url + "#area/" + aid)
    page.wait_for_selector("[data-view=area] .kg-tcard")
    assert page.evaluate("window.__kg.zoom") >= 0.8
    drawn = page.evaluate("window.__kg.maxNodesDrawn")
    assert 1 <= drawn < 25 and "Show all 41" not in page.inner_text("[data-view=area]")
    assert f"Showing the {drawn} most connected of 41 tables" in page.inner_text("[data-view=area]")
    page.click("[data-act=show-all]")                                # show all may zoom lower and pan
    page.wait_for_function(f"window.__kg.maxNodesDrawn > {drawn}")
    assert page.evaluate("window.__kg.maxNodesDrawn") <= 40
    assert not page.errors


def test_csp_allows_the_page_and_the_gzip_decode(page, tmp_path, dump_schema):
    """The Content-Security-Policy meta must not block the inline scripts, styles, fonts, data: images or the
    gzip+base64 decode (DecompressionStream, Blob, Response)."""
    from keygraph.cli import load_graph
    from keygraph.explorer import build_html
    build(tmp_path, dump_schema, context=[str(Path(__file__).parent / "fixtures" / "context.yaml")])
    g = load_graph(tmp_path)
    html = build_html(g, None, gzip_over=10)  # force the compressed data block
    assert 'http-equiv="Content-Security-Policy"' in html and 'id="kg-data">GZ:' in html
    f = tmp_path / "gz.html"
    f.write_text(html, encoding="utf-8")
    console = []
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    for route, view in (("#areas", "areas"), ("#table/shop.orders", "table"), ("#search/paid", "search"),
                        ("#path/shop.customers/shop.products", "path")):
        page.goto(f.as_uri() + route)
        page.wait_for_selector(f"[data-view={view}]")
    assert page.evaluate("document.fonts.check('12px Inter')")
    assert not page.errors and not console, (page.errors, console)
