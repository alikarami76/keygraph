"""The fictional webshop in examples/shop must keep showing what keygraph does."""

import json
from pathlib import Path

import pytest

from keygraph.cli import main

SHOP = Path(__file__).resolve().parents[1] / "examples" / "shop"


def _build(out: Path, profile: bool = False) -> None:
    d = SHOP / "dumps"
    assert main(["load", "--dir", str(out), "--columns", str(d / "columns.json"), "--statistics", str(d / "statistics.json"),
                 "--key-columns", str(d / "key_columns.json"), "--tables", str(d / "tables.json"),
                 "--context", str(SHOP / "context.yaml")]) == 0
    if profile:
        assert main(["profile", "--dir", str(out), "--source", "sqlite", "--url", f"sqlite:///{SHOP / 'shop.db'}", "--bare-tables",
                     "--tables", "shop.customers,shop.orders,shop.order_items,shop.products,billing.payments"]) == 0


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
