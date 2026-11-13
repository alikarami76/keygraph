import json

import pytest
from conftest import needs_yaml

from keygraph import query as Q
from keygraph.graph import AmbiguousTable, Graph, UnknownTable


def test_resolve(graph):
    assert graph.resolve("orders") == "shop.orders"
    assert graph.resolve("SHOP.Orders") == "shop.orders"
    with pytest.raises(UnknownTable):
        graph.resolve("ordrs_x")
    from keygraph.graph import build_graph
    from keygraph.model import Schema, Table
    s = Schema()
    s.add(Table("a", "t"))
    s.add(Table("b", "t"))
    with pytest.raises(AmbiguousTable):
        build_graph(s).resolve("t")


def test_resolve_prefers_the_original_over_mirror_and_copy_schemas():
    from keygraph.graph import build_graph
    from keygraph.model import Schema, Table
    s = Schema()
    for schema in ("shop", "reporting", "replica"):
        s.add(Table(schema, "customers"))
    g = build_graph(s)
    g.data["mirrors"] = {"reporting": "shop"}
    g.data["copy_schemas"] = ["repl*"]
    assert g.resolve("customers") == "shop.customers"
    assert g.resolve("reporting.customers") == "reporting.customers"  # still reachable by full name
    g.data["mirrors"], g.data["copy_schemas"] = {}, []
    with pytest.raises(AmbiguousTable):
        g.resolve("customers")  # without mirror/copy info it stays ambiguous


def test_resolve_two_originals_and_a_mirror_stay_ambiguous():
    from keygraph.graph import build_graph
    from keygraph.model import Schema, Table
    s = Schema()
    for schema in ("shop", "crm", "reporting"):
        s.add(Table(schema, "customers"))
    g = build_graph(s)
    g.data["mirrors"] = {"reporting": "shop"}
    with pytest.raises(AmbiguousTable) as exc:
        g.resolve("customers")
    assert exc.value.candidates == ["crm.customers", "reporting.customers", "shop.customers"]


@needs_yaml
def test_readme_quickstart_path_on_the_shop_example(tmp_path):
    from tests.test_example_shop import _build
    _build(tmp_path)  # examples/shop/out is not committed: build the demo the way the README does
    g = Graph.load(tmp_path / "graph.json")
    assert g.resolve("customers") == "shop.customers"
    assert Q.find_paths(g, g.resolve("customers"), g.resolve("shipments"))


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


def test_path_penalises_hub_fanout_and_log():
    from keygraph import hubs as H
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    pk = [Index("PRIMARY", ["id"], True, True)]
    s.add(Table("app", "users", [Column("id", "int")], pk))
    s.add(Table("app", "orders", [Column("id", "int"), Column("user_id", "int"), Column("shipment_id", "int")], pk))
    s.add(Table("app", "shipments", [Column("id", "int")], pk))
    s.add(Table("app", "order_logs", [Column("id", "int"), Column("order_id", "int"), Column("shipment_id", "int")], pk))
    for i in range(30):
        s.add(Table("app", f"x{i}", [Column("id", "int"), Column("user_id", "int")], pk))
    g = build_graph(s)
    H.mark_hubs(g, H.detect_hubs(g))
    best = Q.find_paths(g, "orders", "shipments", 4, 0.4, 3)[0]
    assert best["tables"] == ["app.orders", "app.shipments"] and best["steps"][0]["warnings"] == []
    via_log = next(p for p in Q.find_paths(g, "orders", "shipments", 4, 0.4, 3) if "app.order_logs" in p["tables"])
    assert "fan_out" in via_log["steps"][0]["warnings"] and "log" in via_log["steps"][0]["warnings"]
    assert via_log["score"] < via_log["confidence"]
    assert via_log["steps"][0]["fan_out"] is True


def test_is_log_uses_name_tokens_not_substrings():
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    pk = [Index("PRIMARY", ["id"], True, True)]
    for n in ("catalog_items", "blog_posts", "dialog", "order_logs", "price_history", "items_bak", "audit_log"):
        s.add(Table("app", n, [Column("id", "int")], pk))
    s.add(Table("catalog", "items", [Column("id", "int")], pk))
    g = build_graph(s)
    flagged = {n: Q._is_log(g, f"app.{n}") for n in
               ("catalog_items", "blog_posts", "dialog", "order_logs", "price_history", "items_bak", "audit_log")}
    assert not flagged["catalog_items"] and not flagged["blog_posts"] and not flagged["dialog"]
    assert all(flagged[n] for n in ("order_logs", "price_history", "items_bak", "audit_log"))
    assert not Q._is_log(g, "catalog.items")


def hub_star():
    """users is a hub (30 x_i point to it); orders -> users, and audit tables reach users too."""
    from keygraph import hubs as H
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    pk = [Index("PRIMARY", ["id"], True, True)]
    s.add(Table("app", "users", [Column("id", "int")], pk))
    s.add(Table("app", "orders", [Column("id", "int"), Column("user_id", "int")], pk))
    s.add(Table("app", "addresses", [Column("id", "int"), Column("user_id", "int")], pk))
    for i in range(30):
        s.add(Table("app", f"x{i}", [Column("id", "int"), Column("user_id", "int")], pk))
    g = build_graph(s)
    H.mark_hubs(g, H.detect_hubs(g))
    assert "app.users" in g.data["hubs"]
    return g


def test_hub_penalty_never_applies_to_the_endpoints():
    g = hub_star()
    to_hub = Q.find_paths(g, "orders", "users", 4, 0.4, 3)[0]
    assert to_hub["tables"] == ["app.orders", "app.users"]
    assert "hub" not in to_hub["steps"][0]["warnings"] and to_hub["score"] == to_hub["confidence"]
    from_hub = Q.find_paths(g, "users", "orders", 4, 0.4, 3)[0]
    assert all("hub" not in s["warnings"] for s in from_hub["steps"])


def test_hub_penalty_applies_to_an_intermediate_hub_once():
    g = hub_star()
    p = Q.find_paths(g, "orders", "addresses", 4, 0.4, 3)[0]
    assert p["tables"] == ["app.orders", "app.users", "app.addresses"]
    assert [s["warnings"] for s in p["steps"]] == [["hub"], ["fan_out"]]
    assert p["score"] == round(p["confidence"] * Q.HUB_PENALTY * Q.FANOUT_PENALTY, 3)


def test_scratch_names_count_as_log_like():
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    pk = [Index("PRIMARY", ["id"], True, True)]
    names = ("orders_test", "mass_cancel_shadow_test", "tmp_orders", "temp_x", "orders_backup", "orders_copy",
             "old_orders", "orders_bak", "contest", "templates", "copyright")
    for n in names:
        s.add(Table("app", n, [Column("id", "int")], pk))
    g = build_graph(s)
    flagged = {n for n in names if Q._is_log(g, f"app.{n}")}
    assert flagged == set(names[:8])  # contest, templates, copyright hold the word only inside a token


def test_paths_are_sorted_by_score():
    g = hub_star()
    ps = Q.find_paths(g, "x1", "addresses", 4, 0.4, 3)
    assert [p["score"] for p in ps] == sorted((p["score"] for p in ps), reverse=True)


def test_near_ties_sort_on_the_unrounded_score():
    """Two routes whose scores round to the same 3 decimals: the truly better one wins, not the shorter one."""
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    from keygraph.query import find_paths
    pk = [Index("PRIMARY", ["id"], True, True)]
    s = Schema()
    s.add(Table("m", "bees", [Column("id", "int")], list(pk)))
    s.add(Table("m", "exes", [Column("id", "int"), Column("bee_id", "int")], list(pk)))
    s.add(Table("m", "ays", [Column("id", "int"), Column("bee_id", "int"), Column("exe_id", "int")], list(pk)))
    g = build_graph(s)
    conf = {("m.ays", "m.bees"): 0.7, ("m.ays", "m.exes"): 0.8367, ("m.exes", "m.bees"): 0.8367}
    for e in g.edges:
        e["confidence"] = conf[(e["from"], e["to"])]
    ps = find_paths(g, "m.ays", "m.bees", 3, 0.6, 2)
    assert [p["score"] for p in ps] == [0.7, 0.7]
    assert ps[0]["hops"] == 2  # 0.8367 * 0.8367 = 0.70007 > 0.7


def test_many_to_many_hop_is_fan_out_and_penalised():
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    pk = [Index("PRIMARY", ["id"], True, True)]
    s.add(Table("app", "users", [Column("id", "int")], pk))
    s.add(Table("app", "orders", [Column("id", "int"), Column("user_id", "int")], pk))
    g = build_graph(s)
    edges = g.data["edges"]
    assert edges, "expected an inferred orders -> users edge"
    for e in edges:
        e["cardinality"] = "many-to-many"
    for a, b in (("orders", "users"), ("users", "orders")):
        p = Q.find_paths(g, a, b, 4, 0.4, 3)[0]
        step = p["steps"][0]
        assert step["cardinality"] == "many-to-many"
        assert step["fan_out"] is True and "fan_out" in step["warnings"]
        assert p["score"] == round(p["confidence"] * Q.FANOUT_PENALTY, 3)


def _weak(graph, monkeypatch, conf=0.55):
    """Lower one edge of orders to a weak inferred relation (and drop the cached adjacency)."""
    es = [e for e in graph.edges if e["from"] == "shop.orders" and e["to"] == "shop.users"]
    for e in es:
        monkeypatch.setitem(e, "confidence", conf)
    monkeypatch.setattr(graph, "_adj", None)
    return len(es)


def test_neighbors_hide_weak_relations_by_default(graph, monkeypatch):
    n = _weak(graph, monkeypatch)
    r = Q.neighbors(graph, "orders", 1)
    assert r["min_confidence"] == 0.6 and r["hidden"] == n
    assert "shop.users" not in {x["to"] for x in r["levels"]["1"]}
    assert f"{n} weaker relation{'s' if n > 1 else ''} hidden (below 0.6); use --min-confidence 0 to see them" in Q.format_neighbors(r)
    all_ = Q.neighbors(graph, "orders", 1, 0.0)
    assert all_["hidden"] == 0 and "shop.users" in {x["to"] for x in all_["levels"]["1"]}
    assert "hidden" not in Q.format_neighbors(all_)


def test_describe_hides_weak_relations_but_brief_is_unchanged(graph, monkeypatch, tmp_path):
    from keygraph import search as S
    from keygraph.context import ContextDB
    n = _weak(graph, monkeypatch)
    with ContextDB(tmp_path / "k.db", graph) as ctx:
        d = S.describe(graph, ctx, "orders")
        assert d["hidden_relations"] == n and "shop.users" not in {x["to"] for x in d["relations"]}
        assert "weaker relation" in S.format_describe(d) and "hidden (below 0.6)" in S.format_describe(d)
        full = S.describe(graph, ctx, "orders", min_confidence=0.0)
        assert full["hidden_relations"] == 0 and "shop.users" in {x["to"] for x in full["relations"]}
        b = S.describe(graph, ctx, "orders", brief=True)
        assert "hidden_relations" not in b and "relations" not in b
