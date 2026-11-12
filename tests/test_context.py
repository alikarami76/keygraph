import base64
import gzip
import json
import re

from conftest import FIX, edge

from keygraph import search as S
from keygraph.cli import main
from keygraph.context import ContextDB, normalize
from keygraph.explorer import build_html
from keygraph.notes import parse_note
from keygraph.report import write_report


def make_ctx(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    ctx.index_graph(graph)
    counts = ctx.import_path(FIX / "context.yaml")
    return ctx, counts


def test_normalize_persian():
    assert normalize("سفارش ۱۲۳ ك ي") == "سفارش 123 ک ی"
    assert normalize("orderItems_id") == "order items id"


def test_import_validates_with_warnings(graph, tmp_path):
    ctx, counts = make_ctx(graph, tmp_path)
    assert counts == {"concepts": 3, "relations": 1, "columns": 2, "metrics": 1, "tables": 1}
    assert any("shop.nope" in w for w in ctx.warnings) and any("missing_col" in w for w in ctx.warnings)
    c = ctx.table_context("shop.orders")
    assert c["notes"]["grain"] == "one row per order" and next(x for x in c["columns"] if x["col"] == "status")["codes"]["2"] == "paid"
    assert c["notes"]["verified"] == 1 and c["notes"]["source"].endswith("context.yaml")


def test_search_ranks_and_expands(graph, tmp_path):
    ctx, _ = make_ctx(graph, tmp_path)
    r = S.search(graph, ctx, "purchase")
    assert r["tables"][0]["table"] == "shop.orders" and r["tables"][0]["grain"] == "one row per order"
    assert "created_at" in r["tables"][0]["filter_columns"]
    r = S.search(graph, ctx, "سفارش")  # Persian synonym
    assert r["tables"][0]["table"] == "shop.orders"
    r = S.search(graph, ctx, "buyer")
    tabs = [t["table"] for t in r["tables"]]
    assert "shop.customers" in tabs and "shop.orders" in tabs  # one hop over concept relation
    r = S.search(graph, ctx, "revenue")
    assert r["metrics"] == ["revenue"] and r["tables"][0]["table"] in ("shop.order_items", "shop.orders")
    r = S.search(graph, ctx, "customer order")
    assert r["join_paths"] and "JOIN" in r["join_paths"][0]["sql"]
    r = S.search(graph, ctx, "commentable")
    assert r["tables"][0]["table"] == "shop.comments"
    assert "pitfalls" in S.search(graph, ctx, "carts")["tables"][0]


def test_describe(graph, tmp_path):
    ctx, _ = make_ctx(graph, tmp_path)
    text = S.format_describe(S.describe(graph, ctx, "orders"))
    assert "one row per order" in text and "2=paid" in text and "primary key: id" in text


def test_relation_strengthens_edge(dump_schema, tmp_path):
    from keygraph.graph import build_graph
    g = build_graph(dump_schema)
    before = edge(g, "shop.orders", ["buyer_user_id"], "shop.users")["confidence"]
    ctx = ContextDB(tmp_path / "r.db", g)
    ctx.upsert("concept", {"name": "user", "links": [{"table": "shop.users", "role": "primary-record"}]}, "t", True)
    ctx.upsert("concept", {"name": "order", "links": [{"table": "shop.orders", "role": "primary-record"}]}, "t", True)
    ctx.upsert("relation", {"from": "user", "rel": "places", "to": "order"}, "t", True)
    n = S.apply_context_to_graph(g, ctx)
    e = edge(g, "shop.orders", ["buyer_user_id"], "shop.users")
    assert n >= 1 and e["confidence"] >= before and any("business relation" in x for x in e["evidence"])
    assert S.apply_context_to_graph(g, ctx) == 0  # idempotent


def test_report_context_coverage(graph, tmp_path):
    ctx, _ = make_ctx(graph, tmp_path)
    r = write_report(graph, ctx)
    assert "## Context coverage" in r and "Unverified context entries" in r and "Tables with no context" in r


def test_notes_parser(tmp_path):
    p = tmp_path / "carts.md"
    p.write_text("---\nkind: table\nengine: shop\ntable: carts\n---\n# t\n**Summary:** dead table\n## Grain & meaning\nOne row per cart.\n"
                 "## Columns\n- `status` - cart state\n## Status codes\n- `1` = open\n- `2` = closed\n## Gotchas\n- never use for conversion\n"
                 "## Verified facts\n- yes\n")
    n = parse_note(p, schema_key="engine")
    assert n["table"] == "shop.carts" and n["grain"] == "One row per cart." and not n["verified"] and n["verified_facts"]
    assert n["columns"][0] == {"column": "status", "meaning": "cart state"} and "never use" in n["pitfalls"]
    assert list(n["codes"].values())[0] == {"1": "open", "2": "closed"}


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["load", "--dir", str(out), "--columns", str(FIX / "shop_columns.json"), "--statistics", str(FIX / "shop_statistics.json"),
                 "--key-columns", str(FIX / "shop_key_columns.json"), "--context", str(FIX / "context.yaml"),
                 "--overrides", str(FIX / "overrides.json")]) == 0
    for f in ("graph.json", "RELATIONS_REPORT.md", "keygraph.html", "keygraph.db"):
        assert (out / f).exists()
    capsys.readouterr()
    assert main(["path", "customers", "products", "--dir", str(out), "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["paths"][0]["hops"] == 3
    assert main(["context", "add", "--kind", "column", "table=shop.orders", "column=created_at", "meaning=when the order was placed",
                 "--dir", str(out)]) == 0
    capsys.readouterr()
    assert main(["search", "when", "placed", "--dir", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tables"][0]["table"] == "shop.orders"
    assert main(["keys", "orders", "--dir", str(out)]) == 0
    assert main(["path", "nope_table", "orders", "--dir", str(out)]) == 2
    assert main(["describe", "t", "--dir", str(out)]) == 2


def decode(html):
    m = re.search(r'<script type="application/octet-stream" id="kg-data">(.*?)</script>', html, re.S)
    raw = m.group(1).strip()
    if raw.startswith("GZ:"):
        raw = gzip.decompress(base64.b64decode(raw[3:])).decode()
    return json.loads(raw)


def test_explorer_embeds_data(graph, tmp_path):
    ctx, _ = make_ctx(graph, tmp_path)
    for limit in (10**9, 10):  # plain and gzip
        html = build_html(graph, ctx.dump(), gzip_over=limit)
        d = decode(html)
        assert len(d["t"]) == len(graph.nodes) and len(d["e"]) == len(graph.edges)
        assert ("id=\"kg-data\">GZ:" in html) == (limit == 10)
        assert "cytoscape.js 3." not in html and "dagre" in html and "KGDiagram" in html
        assert not re.search(r"<script[^>]+src=|<link[^>]+href=[\"']https?:|fetch\(|XMLHttpRequest", html)
    assert any(c["n"] == "order" for c in d["c"]["concepts"]) and d["c"]["tn"][0]["g"] == "one row per order"


def test_browser(graph, tmp_path):
    pytest = __import__("pytest")
    sync = pytest.importorskip("playwright.sync_api")
    ctx, _ = make_ctx(graph, tmp_path)
    f = tmp_path / "k.html"
    f.write_text(build_html(graph, ctx.dump(), gzip_over=10), encoding="utf-8")
    with sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binaries missing
            pytest.skip(f"no browser: {exc}")
        page = b.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f.as_uri())
        page.wait_for_selector("body[data-ready='1']", timeout=20000)
        assert page.evaluate("window.__kg.search('order_items')[0]") == "shop.order_items"
        assert "order" in page.evaluate("window.__kg.search('سفارش')")
        s = page.evaluate("window.__kg.relationSummary('shop.orders')")
        assert any("shop.customers" in g["tables"] for g in s["out"])
        assert any("shop.order_items" in g["tables"] for g in s["in"])
        page.goto(f.as_uri() + "#path/shop.customers/shop.products")
        page.wait_for_selector("[data-step]")
        assert "JOIN shop.orders" in page.inner_text("[data-view=path]")
        page.goto(f.as_uri() + "#areas")
        page.wait_for_selector("[data-view=areas] [data-area]")
        page.fill("[data-search]", "ord")
        assert page.locator(".kg-popover .kg-menu-item").count() >= 1
        assert not errs, errs
        b.close()


def test_curation_roundtrip(tmp_path, graph):
    from keygraph.context import ContextDB
    f = tmp_path / "c.yaml"
    f.write_text("areas:\n  - name: Orders\n    match: [orders]\nhubs:\n  exclude: [shop.users]\n")
    db = ContextDB(tmp_path / "k.db", graph)
    db.import_path(f)
    cur = db.curation()
    assert cur["areas"][0]["name"] == "Orders" and cur["hubs"]["exclude"] == ["shop.users"]


def test_curation_reimport_does_not_duplicate(tmp_path, graph):
    from keygraph.context import ContextDB
    f = tmp_path / "c.yaml"
    f.write_text("areas:\n  - name: Orders\n    match: [orders]\nhubs:\n  exclude: [shop.users]\n")
    db = ContextDB(tmp_path / "k.db", graph)
    db.import_path(f)
    db.import_path(f)
    cur = db.curation()
    assert len(cur["areas"]) == 1 and cur["hubs"]["exclude"] == ["shop.users"]
    f.write_text("areas:\n  - name: Sales\n    match: [orders]\n")
    db.import_path(f)
    cur = db.curation()
    assert [a["name"] for a in cur["areas"]] == ["Sales"] and cur["hubs"] == {"include": [], "exclude": []}


def test_cli_context_args_after_options(tmp_path, capsys):
    """key=value pairs and the path may follow options (Python 3.11 argparse needs help with this)."""
    import pytest
    out = tmp_path / "out"
    assert main(["load", "--dir", str(out), "--columns", str(FIX / "shop_columns.json")]) == 0
    assert main(["context", "add", "--dir", str(out), "--kind", "table", "table=shop.orders", "grain=one order"]) == 0
    assert main(["context", "export", "--dir", str(out), "--out", str(tmp_path / "a.json")]) == 0
    assert "one order" in (tmp_path / "a.json").read_text()
    assert main(["context", "import", "--dir", str(out), "--verified", str(FIX / "context.yaml")]) == 0
    assert main(["context", "export", "--dir", str(out), "--out", str(tmp_path / "b.json")]) == 0
    assert "Person who buys." in (tmp_path / "b.json").read_text()
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["keys", "orders", "--dir", str(out), "extra"])
    with pytest.raises(SystemExit):
        main(["context", "stats", "--dir", str(out), "--bogus"])


def test_context_file_that_is_not_a_mapping_is_a_clean_error(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["load", "--dir", str(out), "--columns", str(FIX / "shop_columns.json")]) == 0
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    assert main(["context", "import", str(bad), "--dir", str(out)]) == 2
    assert "expected a mapping" in capsys.readouterr().err


def _col(ctx, table="shop.orders", col="status"):
    return next(c for c in ctx.table_context(table)["columns"] if c["col"] == col)


def test_unverified_write_never_replaces_verified_row(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "human note"}, "human", True)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "agent guess"}, "agent", False)
    c = _col(ctx)
    assert c["meaning"] == "human note" and c["verified"] == 1 and c["source"] == "human"
    assert ctx.warnings == ["kept verified column note for shop.orders.status (source human); unverified write from agent ignored; pass --verified (or verified=true) if you have confirmed it"]
    # the same source may update its own verified row (a file re-import)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "edited"}, "human", False, allow_same_source=True)
    assert _col(ctx)["meaning"] == "edited" and len(ctx.warnings) == 1


def test_same_source_reimport_and_verified_overwrite(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "v1"}, "a.yaml", False)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "v2"}, "a.yaml", False, allow_same_source=True)
    assert _col(ctx)["meaning"] == "v2"
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "v3"}, "b.yaml", True)
    assert _col(ctx)["meaning"] == "v3" and _col(ctx)["verified"] == 1
    assert ctx.warnings == []


def test_verified_guard_covers_every_kind(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    items = {
        "concept": {"name": "Order", "definition": "x"},
        "link": {"concept": "Order", "table": "shop.orders", "column": "", "role": "primary-record"},
        "relation": {"from": "Order", "rel": "related-to", "to": "Customer"},
        "metric": {"name": "gmv", "definition": "x"},
        "table": {"table": "shop.orders", "grain": "x"},
    }
    for kind, d in items.items():
        ctx.upsert(kind, d, "human", True)
    n = len(ctx.warnings)
    for kind, d in items.items():
        ctx.upsert(kind, {**d, "note": "n", "definition": "y", "grain": "y"}, "agent", False)
    assert len(ctx.warnings) == n + len(items)
    assert ctx.rows("concepts")[0]["definition"] == "x" and ctx.rows("metrics")[0]["definition"] == "x"
    assert ctx.table_context("shop.orders")["notes"]["grain"] == "x"
    assert ctx.rows("concept_links")[0]["note"] == ""


def test_cli_context_add_reports_kept_verified(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["load", "--dir", str(out), "--columns", str(FIX / "shop_columns.json"), "--statistics", str(FIX / "shop_statistics.json"),
                 "--key-columns", str(FIX / "shop_key_columns.json")]) == 0
    base = ["context", "add", "--kind", "column", "table=shop.orders", "column=created_at", "--dir", str(out)]
    assert main(base + ["meaning=human note", "verified=true", "source=human"]) == 0
    capsys.readouterr()
    assert main(base + ["meaning=agent guess"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert any("kept verified column note for shop.orders.created_at (source human)" in w for w in j["warnings"])
    assert main(["describe", "orders", "--dir", str(out), "--json"]) == 0
    assert "human note" in capsys.readouterr().out


def test_default_source_unverified_add_never_demotes_verified_row(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    d = {"table": "shop.orders", "column": "status", "meaning": "checked"}
    assert ctx.upsert("column", d, "", True) is True  # no source: stored as "unknown"
    assert ctx.upsert("column", {**d, "meaning": "guess"}, "", False) is False
    assert _col(ctx)["meaning"] == "checked" and _col(ctx)["verified"] == 1
    assert len(ctx.warnings) == 1 and "kept verified column note" in ctx.warnings[0]


def test_old_absolute_source_compares_by_safe_name(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "v1"}, "a.yaml", True)
    ctx.db.execute("UPDATE column_semantics SET source='/home/me/notes/a.yaml'")
    ctx.db.commit()
    assert ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "v2"}, "a.yaml", False, allow_same_source=True)
    assert ctx.warnings == [] and _col(ctx)["meaning"] == "v2"


def test_import_counts_only_rows_written(graph, tmp_path):
    ctx = ContextDB(tmp_path / "k.db", graph)
    ctx.upsert("column", {"table": "shop.orders", "column": "status", "meaning": "human"}, "human", True)
    f = tmp_path / "other.json"
    f.write_text(json.dumps({"columns": [{"table": "shop.orders", "column": "status", "meaning": "x"}]}), encoding="utf-8")
    assert ctx.import_path(f)["columns"] == 0
    assert _col(ctx)["meaning"] == "human"


def test_yaml_context_without_pyyaml_is_a_clear_error(graph, tmp_path, monkeypatch):
    import pytest

    from keygraph.context import read_copy_globs
    f = tmp_path / "c.yaml"
    f.write_text("copies: ['ods_*']\n", encoding="utf-8")
    monkeypatch.setitem(__import__("sys").modules, "yaml", None)
    ctx = ContextDB(tmp_path / "k.db", graph)
    with pytest.raises(ValueError, match=r"needs PyYAML: pip install 'keygraph\[yaml\]'"):
        ctx.import_path(f)
    with pytest.raises(ValueError, match="needs PyYAML"):
        read_copy_globs([f])
    j = tmp_path / "c.json"
    j.write_text('{"copies": ["ods_*"]}', encoding="utf-8")
    assert read_copy_globs([j]) == ["ods_*"]


def test_missing_yaml_error_names_the_file_and_is_typed(graph, tmp_path, monkeypatch):
    import pytest

    from keygraph.context import YamlMissing
    f = tmp_path / "notes.yaml"
    f.write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setitem(__import__("sys").modules, "yaml", None)
    with pytest.raises(YamlMissing, match="notes.yaml"):
        ContextDB(tmp_path / "k.db", graph).import_path(f)
