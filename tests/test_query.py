import json

import pytest

from keygraph import query as Q
from keygraph.graph import AmbiguousTable, Graph, UnknownTable


def test_resolve(graph):
    assert graph.resolve("orders") == "shop.orders"
    assert graph.resolve("SHOP.Orders") == "shop.orders"
    with pytest.raises(UnknownTable):
        graph.resolve("ordrs_x")
    from keygraph.model import Schema, Table
    from keygraph.graph import build_graph
    s = Schema()
    s.add(Table("a", "t"))
    s.add(Table("b", "t"))
    with pytest.raises(AmbiguousTable):
        build_graph(s).resolve("t")


def test_path_ranking_and_sql(graph):
    ps = Q.find_paths(graph, "customers", "products", 4, 0.6, 3)
    assert ps[0]["tables"] == ["shop.customers", "shop.orders", "shop.order_items", "shop.products"]
    assert ps[0]["hops"] == 3
    confs = [p["confidence"] for p in ps]
    assert confs == sorted(confs, reverse=True)
    sql = ps[0]["sql"]
    assert "FROM shop.customers AS c" in sql
    assert "JOIN shop.orders AS o ON c.id = o.customer_id" in sql
    assert "JOIN shop.order_items AS oi ON o.id = oi.order_id" in sql
    assert "JOIN shop.products AS p ON oi.product_id = p.id" in sql
    assert any(s["fan_out"] for s in ps[0]["steps"])
    assert Q.find_paths(graph, "customers", "settings") == []
    text = Q.format_paths("customers", "products", ps)
    assert "Path 1" in text and "declared foreign key" in text


def test_min_confidence_prunes(graph):
    assert Q.find_paths(graph, "customers", "products", 4, 0.99) == []


def test_self_join_aliases():
    assert Q.aliases_for(["a.order_items", "a.orders", "a.order_items"]) == ["oi", "o", "oi2"]


def test_neighbors(graph):
    r = Q.neighbors(graph, "orders", 1)
    assert {x["to"] for x in r["levels"]["1"]} >= {"shop.customers", "shop.order_items", "shop.users"}
    r2 = Q.neighbors(graph, "orders", 2)
    assert "shop.products" in {x["to"] for x in r2["levels"]["2"]}


def test_explain_and_keys(graph):
    x = Q.explain(graph, "shop.orders.customer_id")
    assert x["references"][0]["table"] == "shop.customers" and x["in_indexes"][0]["name"] == "ix_customer"
    y = Q.explain(graph, "orders.id")
    assert y["in_primary_key"] and len(y["referenced_by"]) >= 2
    k = Q.keys(graph, "order_items")
    assert k["primary_key"] == ["id"]
    assert [i["columns"] for i in k["indexes"] if i["name"] == "ix_order_product"] == [["order_id", "product_id"]]
    assert "redundant" in Q.format_keys(k)


def test_json_roundtrip(graph, tmp_path):
    graph.save(tmp_path / "g.json")
    g2 = Graph.load(tmp_path / "g.json")
    assert g2.data["stats"] == graph.data["stats"]
    json.dumps(Q.find_paths(g2, "orders", "products"))
