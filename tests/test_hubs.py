from keygraph.graph import build_graph
from keygraph.model import Column, Index, Schema, Table
from keygraph import hubs as H


def star(n_children=50) -> Schema:
    """One users hub that every child points to through created_by_id and user_id."""
    s = Schema()
    s.add(Table("app", "users", [Column("id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    s.add(Table("app", "orders", [Column("id", "int"), Column("customer_id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    s.add(Table("app", "customers", [Column("id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    for i in range(n_children):
        s.add(Table("app", f"thing{i}", [Column("id", "int"), Column("created_by_id", "int"), Column("user_id", "int")],
                    [Index("PRIMARY", ["id"], True, True)]))
    return s


def star_schema(n_children=50):
    return build_graph(star(n_children))


def test_role_column():
    assert H.is_role_column("created_by_id") and H.is_role_column("updated_by")
    assert not H.is_role_column("customer_id")


def test_detects_users_as_hub_and_marks_edges():
    g = star_schema()
    hubs = H.detect_hubs(g)
    assert "app.users" in hubs and "app.customers" not in hubs
    H.mark_hubs(g, hubs)
    into_users = [e for e in g.edges if e["to"] == "app.users"]
    assert into_users and all(e["hub"] for e in into_users)
    assert any(e["role"] for e in into_users) and g.data["hubs"] == sorted(hubs)
    assert all(not e["hub"] for e in g.edges if e["to"] == "app.customers")


def test_include_exclude():
    g = star_schema()
    assert "app.users" not in H.detect_hubs(g, exclude=["app.users"])
    assert "app.customers" in H.detect_hubs(g, include=["app.customers"])
