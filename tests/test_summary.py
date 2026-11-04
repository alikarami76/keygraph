from keygraph import areas as A
from keygraph.graph import Graph
from keygraph.summary import GROUP_COLLAPSE, relation_summary
from tests.test_areas import two_islands


def edges_graph(tables, edges):
    """Hand-built graph. edges: (from, to, from_cols, conf, hub, role); to_columns is the same as from_columns."""
    nodes = [{"id": t, "schema": t.split(".")[0], "table": t.split(".")[1]} for t in tables]
    es = []
    for frm, to, cols, conf, hub, role in edges:
        es.append({"from": frm, "to": to, "from_columns": list(cols), "to_columns": ["id"] * len(cols),
                   "confidence": conf, "hub": hub, "role": role})
    return Graph({"nodes": nodes, "edges": es})


def test_incoming_grouped_by_column_and_collapsed():
    g = two_islands()
    areas = A.cluster_areas(g)
    s = relation_summary(g, "app.orders", areas)
    grp = next(x for x in s["in"] if x["column"] == "order_id")
    assert grp["count"] == 3 and set(grp["tables"]) == {"app.order_items", "app.order_notes", "app.order_refunds"}
    assert grp["collapsed"] is False


def test_hub_edges_become_labels():
    g = two_islands()
    s = relation_summary(g, "app.orders", A.cluster_areas(g))
    hub = next(h for h in s["hub_labels"] if h["to"] == "app.users")
    assert hub["columns"] == ["created_by_id"] and hub["count"] == 1
    assert all(x["column"] != "created_by_id" for x in s["out"])


def test_users_side_groups_many_children():
    g = two_islands()
    s = relation_summary(g, "app.users", A.cluster_areas(g))
    big = max(s["in"], key=lambda x: x["count"])
    assert big["count"] >= 30 and big["collapsed"]


def test_min_conf_excludes_low_confidence_edge():
    g = edges_graph(["app.a", "app.b", "app.c"], [
        ("app.b", "app.a", ["a_id"], 0.9, False, False),
        ("app.c", "app.a", ["a_id"], 0.5, False, False),
    ])
    s = relation_summary(g, "app.a", [])
    assert [x["tables"] for x in s["in"]] == [["app.b"]]
    assert s["in"][0]["edges"] == ["app.b(a_id)->app.a(id)"]


def test_two_edges_from_same_far_table_count_once():
    g = edges_graph(["app.a", "app.b"], [
        ("app.b", "app.a", ["a_id"], 0.9, False, False),
        ("app.b", "app.a", ["a_id"], 0.8, False, False),
    ])
    s = relation_summary(g, "app.a", [])
    grp = s["in"][0]
    assert grp["count"] == 1 and grp["tables"] == ["app.b"]
    assert len(grp["edges"]) == 2


def test_outgoing_role_edge_to_non_hub_becomes_label():
    g = edges_graph(["app.a", "app.staff"], [
        ("app.a", "app.staff", ["approved_by_id"], 0.9, False, True),
    ])
    s = relation_summary(g, "app.a", [])
    assert s["out"] == []
    assert s["hub_labels"] == [{"to": "app.staff", "columns": ["approved_by_id"], "count": 1}]


def test_incoming_role_edge_stays_in_groups():
    g = edges_graph(["app.a", "app.staff"], [
        ("app.staff", "app.a", ["approved_by_id"], 0.9, False, True),
    ])
    s = relation_summary(g, "app.a", [])
    assert s["hub_labels"] == []
    assert [x["column"] for x in s["in"]] == ["approved_by_id"]


def test_tables_ordered_by_confidence_desc():
    g = edges_graph(["app.a", "app.low", "app.high", "app.mid"], [
        ("app.low", "app.a", ["a_id"], 0.7, False, False),
        ("app.high", "app.a", ["a_id"], 0.95, False, False),
        ("app.mid", "app.a", ["a_id"], 0.8, False, False),
    ])
    s = relation_summary(g, "app.a", [])
    assert s["in"][0]["tables"] == ["app.high", "app.mid", "app.low"]


def test_group_area_is_far_tables_area():
    g = edges_graph(["app.a", "app.b"], [("app.b", "app.a", ["a_id"], 0.9, False, False)])
    areas = [{"id": "a_far", "tables": ["app.b"]}, {"id": "a_home", "tables": ["app.a"]}]
    s = relation_summary(g, "app.a", areas)
    assert s["area"] == "a_home"
    assert s["in"][0]["area"] == "a_far"


def test_collapse_uses_distinct_table_count():
    far = [f"app.f{i}" for i in range(GROUP_COLLAPSE + 1)]
    g = edges_graph(["app.a"] + far, [(t, "app.a", ["a_id"], 0.9, False, False) for t in far])
    grp = relation_summary(g, "app.a", [])["in"][0]
    assert grp["count"] == GROUP_COLLAPSE + 1 and grp["collapsed"] is True
