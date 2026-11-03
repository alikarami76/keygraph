from conftest import edge

from keygraph.graph import build_graph
from keygraph.overrides import Overrides
from keygraph.adapters.dump import load_dump, read_rows
from conftest import FIX


def test_declared_fk(graph):
    e = edge(graph, "shop.orders", ["customer_id"], "shop.customers")
    assert e["kind"] == "declared" and e["confidence"] == 1.0 and e["cardinality"] == "many-to-one"


def test_naming_and_plural(graph):
    e = edge(graph, "shop.order_items", ["order_id"], "shop.orders")
    assert e["kind"] == "inferred" and 0.8 <= e["confidence"] <= 0.95
    assert any("matches table name" in x for x in e["evidence"])
    e = edge(graph, "shop.order_items", ["product_id"], "shop.products")
    assert e and any("compatible types" in x for x in e["evidence"])
    assert edge(graph, "shop.products", ["category_id"], "shop.categories")


def test_role_prefix_and_by_id(graph):
    assert edge(graph, "shop.orders", ["buyer_user_id"], "shop.users")
    e = edge(graph, "shop.orders", ["created_by_id"], "shop.users")
    assert e and any("user reference" in x for x in e["evidence"])


def test_self_reference(graph):
    e = edge(graph, "shop.categories", ["parent_id"], "shop.categories")
    assert e and any("self-reference" in x for x in e["evidence"])


def test_polymorphic(graph):
    n = graph.nodes["shop.comments"]
    assert n["polymorphic"][0]["id_column"] == "commentable_id"
    assert not [e for e in graph.edges if e["from"] == "shop.comments"]


def test_ambiguity(graph):
    es = [e for e in graph.edges if e["from"] == "shop.vendor_notes"]
    assert {e["to"] for e in es} == {"shop.vendors", "shop.tbl_vendors"}
    assert all(e["ambiguous"] for e in es)


def test_cross_schema_penalty(graph):
    e = edge(graph, "logs.access_log", ["user_id"], "shop.users")
    assert e and any("cross-schema" in x for x in e["evidence"])
    assert e["confidence"] < edge(graph, "shop.order_items", ["order_id"], "shop.orders")["confidence"]


def test_no_pk_and_redundant_index(graph):
    assert graph.nodes["shop.scratch"]["primary_key"] == []
    red = graph.nodes["shop.order_items"]["redundant_indexes"]
    assert red and red[0]["index"] == "ix_order" and red[0]["covered_by"] == "ix_order_product"


def test_overrides(dump_schema):
    g = build_graph(dump_schema, Overrides.load(FIX / "overrides.json"))
    e = edge(g, "shop.vendor_notes", ["vendor_id"], "shop.vendors")
    assert e["status"] == "confirmed" and not e["ambiguous"]
    assert edge(g, "shop.vendor_notes", ["vendor_id"], "shop.tbl_vendors") is None
    assert edge(g, "logs.access_log", ["order_id"], "shop.orders") is None
    assert edge(g, "shop.scratch", ["a"], "shop.users")["status"] == "added"
    assert g.data["overrides"] == {"confirmed": 1, "rejected": 1, "added": 1}


def test_fallback_without_statistics():
    sch = load_dump(FIX / "shop_columns.json")
    assert sch.tables["shop.orders"].primary_key == ["id"]
    assert not sch.tables["shop.orders"].foreign_keys
    g = build_graph(sch)
    assert edge(g, "shop.orders", ["customer_id"], "shop.customers")["kind"] == "inferred"


def test_field_mapping_and_flatten():
    rows = read_rows(FIX / "shop_columns.jsonl")
    sch = load_dump(rows, columns_map={"_flatten": ["raw"], "TABLE_SCHEMA": "engine"})
    assert "shop.orders" in sch.tables and "logs.access_log" in sch.tables
    assert sch.tables["shop.orders"].primary_key == ["id"]


def test_graph_stable(dump_schema):
    a = build_graph(dump_schema).data
    b = build_graph(dump_schema).data
    assert a == b
    assert [e["id"] for e in a["edges"]] == sorted(e["id"] for e in a["edges"])


def test_composite_declared_fk():
    from keygraph.model import Schema, Table, Column, Index, ForeignKey
    s = Schema()
    a = Table("m", "a", [Column("x", "int"), Column("y", "int")], [Index("PRIMARY", ["x", "y"], True, True)])
    b = Table("m", "b", [Column("id", "int"), Column("ax", "int"), Column("ay", "int")], [Index("PRIMARY", ["id"], True, True)],
              [ForeignKey("fk", ["ax", "ay"], "m", "a", ["x", "y"])])
    s.add(a)
    s.add(b)
    g = build_graph(s)
    e = g.edges[0]
    assert e["from_columns"] == ["ax", "ay"] and e["to_columns"] == ["x", "y"] and e["confidence"] == 1.0
