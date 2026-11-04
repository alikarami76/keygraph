from keygraph import areas as A
from keygraph import hubs as H
import itertools

from keygraph.graph import Graph, build_graph
from keygraph.model import Column, Index, Schema, Table


def pk(*cols):
    return [Index("PRIMARY", ["id"], True, True)]


def two_islands():
    s = Schema()
    s.add(Table("app", "users", [Column("id", "int")], pk()))
    for t, refs in {"orders": [], "order_items": ["order_id"], "order_notes": ["order_id"], "order_refunds": ["order_id"],
                    "vouchers": [], "voucher_codes": ["voucher_id"], "voucher_usages": ["voucher_id", "voucher_code_id"]}.items():
        cols = [Column("id", "int"), Column("created_by_id", "int")] + [Column(r, "int") for r in refs]
        s.add(Table("app", t, cols, pk()))
    for i in range(30):  # make users a hub
        s.add(Table("app", f"misc{i}", [Column("id", "int"), Column("user_id", "int")], pk()))
    g = build_graph(s)
    H.mark_hubs(g, H.detect_hubs(g))
    return g


def test_islands_become_areas_and_hubs_do_not_glue_them():
    g = two_islands()
    areas = A.cluster_areas(g)
    of = A.area_of(areas)
    assert of["app.order_items"] == of["app.orders"] == of["app.order_notes"]
    assert of["app.voucher_codes"] == of["app.vouchers"]
    assert of["app.orders"] != of["app.vouchers"]


def test_deterministic_and_stable_ids():
    a1 = A.cluster_areas(two_islands())
    a2 = A.cluster_areas(two_islands())
    assert [(a["id"], a["tables"]) for a in a1] == [(a["id"], a["tables"]) for a in a2]
    assert all(a["id"].startswith("a_") and len(a["id"]) == 10 for a in a1)


def test_names_from_prefix_and_unconnected():
    areas = A.cluster_areas(two_islands())
    names = {a["name"] for a in areas}
    assert "Order" in names and "Voucher" in names
    assert any(a["name"] == "Unconnected (app)" for a in areas)


def graph_of(tables, edges):
    """Minimal graph: tables as "schema.name"; edges as (from, to, confidence) or (from, to, confidence, hub, role)."""
    nodes = [{"id": t, "schema": t.split(".")[0], "table": t.split(".")[1]} for t in tables]
    es = []
    for e in edges:
        hub, role = (e[3], e[4]) if len(e) == 5 else (False, False)
        es.append({"from": e[0], "to": e[1], "confidence": e[2], "hub": hub, "role": role})
    return Graph({"nodes": nodes, "edges": es})


def clique(names, c=1.0):
    return [(a, b, c) for a, b in itertools.combinations(names, 2)]


def stranded_small_areas(g, areas, min_size=3):
    """Small areas (not Unconnected) that still have a plain or confidence edge leaving them."""
    bad = []
    for a in areas:
        if a["size"] >= min_size or a["name"].startswith("Unconnected"):
            continue
        mine = set(a["tables"])
        if any((e["from"] in mine) != (e["to"] in mine) for e in g.edges if not (e.get("hub") or e.get("role"))):
            bad.append(a["name"])
    return bad


def test_area_links_ignore_hub_and_role_edges():
    A_ = ["app.a1", "app.a2", "app.a3"]
    B_ = ["app.b1", "app.b2", "app.b3"]
    g = graph_of(A_ + B_, clique(A_) + clique(B_) + [
        ("app.a1", "app.b1", 0.2),            # plain cross edge 1 (weak, so the blocks stay apart)
        ("app.a2", "app.b2", 0.2),            # plain cross edge 2
        ("app.a3", "app.b3", 1.0, True, False),   # hub edge: must not count
        ("app.a1", "app.b3", 1.0, False, True),   # role edge: must not count
    ])
    areas = A.cluster_areas(g)
    of = A.area_of(areas)
    assert of["app.a1"] == of["app.a2"] == of["app.a3"]
    assert of["app.b1"] == of["app.b2"] == of["app.b3"]
    assert of["app.a1"] != of["app.b1"]
    links = A.area_links(g, areas)
    assert len(links) == 1
    assert links[0]["edges"] == 2
    assert {links[0]["a"], links[0]["b"]} == {of["app.a1"], of["app.b1"]}


def test_small_group_is_absorbed_by_neighbouring_area():
    X = [f"app.x{i}" for i in range(4)]
    g = graph_of(X + ["app.s1", "app.s2"], clique(X) + [("app.s1", "app.s2", 1.0), ("app.s1", "app.x1", 1.0),
                                                       ("app.s2", "app.x2", 1.0)])
    areas = A.cluster_areas(g)
    of = A.area_of(areas)
    assert of["app.s1"] == of["app.s2"] == of["app.x1"]
    assert stranded_small_areas(g, areas) == []


def test_small_group_whose_heaviest_neighbour_is_small_is_not_stranded():
    X = [f"app.x{i}" for i in range(4)]
    tables = X + ["app.p1", "app.p2", "app.q1", "app.q2"]
    g = graph_of(tables, clique(X) + clique(["app.p1", "app.p2"]) + clique(["app.q1", "app.q2"]) + [
        ("app.p1", "app.q1", 0.9),   # heaviest link goes to another small group
        ("app.p2", "app.x1", 0.5),
        ("app.q2", "app.x2", 0.5),
    ])
    areas = A.cluster_areas(g)
    assert stranded_small_areas(g, areas) == []


def test_chained_small_groups_do_not_strand_a_small_target():
    X = [f"app.x{i}" for i in range(4)]
    tables = X + ["app.p1", "app.p2", "app.u1"]
    g = graph_of(tables, clique(X) + clique(["app.p1", "app.p2"]) + [
        ("app.p2", "app.u1", 0.9),   # small group's heaviest neighbour is a single table
        ("app.p1", "app.x1", 0.3),
    ])
    areas = A.cluster_areas(g)
    assert stranded_small_areas(g, areas) == []


def test_status_prefix_is_not_stemmed_to_statu():
    g = graph_of(["app.status_codes", "app.status_logs", "app.status_history"],
                 clique(["app.status_codes", "app.status_logs", "app.status_history"]))
    names = {a["name"] for a in A.cluster_areas(g)}
    assert "Status" in names
    assert "Statu" not in names



def test_curation_rename_pin_exclude_merge():
    g = two_islands()
    areas = A.cluster_areas(g)
    order_id = A.area_of(areas)["app.orders"]
    rules = [{"name": "Orders", "match": [order_id], "pin": ["app.vouchers"], "exclude": ["app.order_refunds"]},
             {"merge": ["Orders", "Voucher"]}, {"name": "Ghost", "match": ["a_deadbeef"]}]
    out, warns = A.apply_curation(g, areas, rules)
    of = A.area_of(out)
    orders = next(a for a in out if a["name"] == "Orders")
    assert orders["auto"] is False and "app.vouchers" in orders["tables"] and "app.voucher_codes" in orders["tables"]
    assert of["app.order_refunds"] != orders["id"]
    assert any("a_deadbeef" in w for w in warns)


def test_curation_unknown_tables_warn_not_raise():
    g = two_islands()
    areas = A.cluster_areas(g)
    order_id = A.area_of(areas)["app.orders"]
    rules = [{"name": "Orders", "match": [order_id], "pin": ["app.nope"], "exclude": ["nope"]}]
    out, warns = A.apply_curation(g, areas, rules)
    assert len(warns) == 2 and any("nope" in w for w in warns)
    assert A.area_of(out)["app.orders"] == order_id


def test_name_is_the_most_connected_non_hub_table_unless_a_prefix_covers_60_percent():
    s = Schema()
    s.add(Table("app", "users", [Column("id", "int")], pk()))
    # a seller area: marketplace_sellers is the most connected; only 2 of 5 tables start with "supply"
    s.add(Table("app", "marketplace_sellers", [Column("id", "int")], pk()))
    for t in ("supply_categories", "supply_contracts", "seller_wallets", "seller_payouts"):
        s.add(Table("app", t, [Column("id", "int"), Column("marketplace_seller_id", "int")], pk()))
    # an order area: 3 of 4 tables start with "order"
    s.add(Table("app", "orders", [Column("id", "int")], pk()))
    for t in ("order_items", "order_notes", "invoices"):
        s.add(Table("app", t, [Column("id", "int"), Column("order_id", "int")], pk()))
    for i in range(30):
        s.add(Table("app", f"misc{i}", [Column("id", "int"), Column("user_id", "int")], pk()))
    g = build_graph(s)
    H.mark_hubs(g, H.detect_hubs(g))
    names = {a["name"]: a for a in A.cluster_areas(g)}
    assert "marketplace_sellers" in names and "app.supply_categories" in names["marketplace_sellers"]["tables"]
    assert "Order" in names


def test_malformed_curation_file_warns_instead_of_crashing(tmp_path, dump_schema):
    """User-written curation YAML: rules without name/match, hubs or mirrors given as lists become warnings."""
    import json as _json
    from keygraph.cli import run_pipeline
    f = tmp_path / "bad.yaml"
    f.write_text("areas:\n  - {pin: [orders]}\n  - just a string\n  - {name: Sales, match: orders, pin: customers}\n"
                 "hubs: [customers]\nmirrors: [reporting]\n", encoding="utf-8")
    out = tmp_path / "out"
    run_pipeline(dump_schema, out, context=[str(f)], verbose=False)
    g = _json.loads((out / "graph.json").read_text())
    w = "\n".join(g["warnings"])
    assert "it needs a name, a match or a merge" in w
    assert "expected a mapping with name, match or merge" in w
    assert "hubs ignored" in w and "mirrors ignored" in w
    assert any(a["name"] == "Sales" for a in g["areas"])  # the good rule still applies (match given as a string)



def test_apply_curation_skips_rule_without_name_or_match():
    g = two_islands()
    areas, warns = A.apply_curation(g, A.cluster_areas(g), [{"pin": ["orders"]}, "oops", {"name": "Vouchers", "match": ["app.vouchers"]}])
    assert len(warns) == 2 and all("needs a name, a match or a merge" in w for w in warns)
    assert any(a["name"] == "Vouchers" for a in areas)
