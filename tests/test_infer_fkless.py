"""Databases without foreign keys: assumed 'id' key, log tables, edges copied onto mirror schemas."""
import json

from keygraph.graph import build_graph
from keygraph.mirrors import mark_mirrors, transfer_mirror_edges
from keygraph.model import Column, ForeignKey, Index, Schema, Table

PK = [Index("PRIMARY", ["id"], True, True)]


def _t(schema, name, *cols, keyed=True):
    return Table(schema, name, [Column("id", "int", False, 1, "PRI" if keyed else "")]
                 + [Column(c, "int", True, i + 2) for i, c in enumerate(cols)], list(PK) if keyed else [])


def _graph(*tables):
    s = Schema()
    for t in tables:
        s.add(t)
    return build_graph(s)


def _edge(g, frm, col, to):
    return next((e for e in g.edges if e["from"] == frm and e["from_columns"] == [col] and e["to"] == to), None)


def test_keyless_target_gets_an_edge_slightly_below_a_keyed_one():
    keyless = _edge(_graph(_t("shop", "customers", keyed=False), _t("shop", "orders", "customer_id")),
                    "shop.orders", "customer_id", "shop.customers")
    keyed = _edge(_graph(_t("shop", "customers"), _t("shop", "orders", "customer_id")),
                  "shop.orders", "customer_id", "shop.customers")
    assert keyless and keyed
    assert any("assumed key: no keys declared, 'id' column" in x for x in keyless["evidence"])
    assert keyless["to_columns"] == ["id"]
    assert 0.05 <= round(keyed["confidence"] - keyless["confidence"], 2) <= 0.15


def test_keyless_table_without_id_is_still_not_a_target_and_does_not_crash():
    t = Table("shop", "customers", [Column("code", "int")], [])
    g = _graph(t, _t("shop", "orders", "customer_id"), Table("shop", "raw", [Column("customer_id", "int")], []))
    assert _edge(g, "shop.orders", "customer_id", "shop.customers") is None
    assert not [e for e in g.edges if e["to"] == "shop.customers"]


def test_log_table_points_at_its_subject():
    g = _graph(_t("shop", "orders"), _t("shop", "order_history", "entity_id"), _t("shop", "item_log", "record_id"),
               _t("shop", "items"))
    e = _edge(g, "shop.order_history", "entity_id", "shop.orders")
    assert e and any("log table 'order_history' names its subject table; 'entity_id' points at it" in x for x in e["evidence"])
    assert _edge(g, "shop.item_log", "record_id", "shop.items")


def test_log_table_in_a_log_schema_finds_the_base_schema():
    g = _graph(_t("app", "invoices"), _t("app_log", "invoices_log", "object_id"))
    assert _edge(g, "app_log.invoices_log", "object_id", "app.invoices")


def test_log_table_without_base_gets_no_edge():
    g = _graph(_t("shop", "orders"), _t("shop", "ghost_audit", "entity_id"))
    assert not [e for e in g.edges if e["from"] == "shop.ghost_audit"]


def _mirrored():
    """The original declares a key; its copy has no keys and a column the name rules cannot read."""
    fk = ForeignKey("fk_buyer", ["buyer"], None, "customers", ["id"])
    orders = Table("shop", "orders", [Column("id", "int"), Column("buyer", "int")], list(PK), [fk])
    tabs = [_t("shop", "customers"), orders, _t("shop", "products"), _t("shop", "order_items", "order_id")]
    tabs += [Table("shop_copy", t.name, [Column(c.name, "int") for c in t.columns], []) for t in list(tabs)]
    g = _graph(*tabs)
    mark_mirrors(g, {"shop_copy": "shop"})
    return g


def test_edges_are_copied_onto_the_mirror_once():
    g = _mirrored()
    assert _edge(g, "shop_copy.orders", "buyer", "shop_copy.customers") is None
    n = transfer_mirror_edges(g)
    e = _edge(g, "shop_copy.orders", "buyer", "shop_copy.customers")
    assert e and e["source"] == "mirror" and e["status"] == "inferred"
    orig = _edge(g, "shop.orders", "buyer", "shop.customers")
    assert e["confidence"] == round(orig["confidence"] * 0.95, 2)
    assert e["evidence"] == ["copy of shop.orders(buyer) -> shop.customers(id) in the original schema"]
    assert transfer_mirror_edges(g) == 0
    ids = [x["id"] for x in g.edges]
    assert len(ids) == len(set(ids))
    assert n >= 1


def test_builds_are_deterministic():
    def run():
        g = _mirrored()
        transfer_mirror_edges(g)
        return json.dumps(g.edges, sort_keys=True)
    assert run() == run()
