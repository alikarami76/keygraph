"""Mirror schemas: a schema whose tables copy another schema's tables is kept out of areas, hubs and paths."""
import json

from keygraph import query as Q
from keygraph.cli import main, run_pipeline
from keygraph.graph import Graph
from keygraph.mirrors import detect_mirrors
from keygraph.model import Column, ForeignKey, Index, Schema, Table

PK = [Index("PRIMARY", ["id"], True, True)]
SHOP = {"customers": ["id", "name"], "orders": ["id", "customer_id", "status"], "order_items": ["id", "order_id", "product_id"],
        "products": ["id", "title"], "reviews": ["id", "product_id", "customer_id"]}


def twins(copy_share=1.0):
    s = Schema()
    for t, cols in SHOP.items():  # the original carries its foreign-key indexes; the copy has primary keys only
        s.add(Table("shop", t, [Column(c, "int") for c in cols], list(PK) + [Index(f"ix_{t}_{c}", [c]) for c in cols if c.endswith("_id")]))
    for i, (t, cols) in enumerate(SHOP.items()):
        if i < round(len(SHOP) * copy_share):
            s.add(Table("shop_copy", t, [Column(c, "int") for c in cols], list(PK)))
        else:
            s.add(Table("shop_copy", t + "_other", [Column("id", "int"), Column("x", "int")], list(PK)))
    # two shop tables that only meet through the copy (declared keys into it)
    s.add(Table("shop", "events", [Column("id", "int"), Column("copied_order", "int")], list(PK),
                [ForeignKey("fk_ev", ["copied_order"], "shop_copy", "orders", ["id"])]))
    s.add(Table("shop", "legacy", [Column("id", "int"), Column("copied_customer", "int")], list(PK),
                [ForeignKey("fk_lg", ["copied_customer"], "shop_copy", "customers", ["id"])]))
    return s


def test_identical_schema_is_a_mirror_of_the_first(tmp_path):
    run_pipeline(twins(), tmp_path, verbose=False)
    g = Graph.load(tmp_path / "graph.json")
    assert g.data["mirrors"] == {"shop_copy": "shop"}
    assert g.nodes["shop_copy.orders"]["mirror_of"] == "shop" and "mirror_of" not in g.nodes["shop.orders"]


def test_threshold_is_80_percent():
    from keygraph.graph import build_graph
    assert detect_mirrors(build_graph(twins(0.8))) == {"shop_copy": "shop"}
    assert detect_mirrors(build_graph(twins(0.6))) == {}


def test_mirrors_stay_out_of_areas_links_and_paths(tmp_path):
    run_pipeline(twins(), tmp_path, verbose=False)
    g = Graph.load(tmp_path / "graph.json")
    mirror_tables = {t for t in g.nodes if t.startswith("shop_copy.")}
    business = [a for a in g.data["areas"] if not a.get("mirror")]
    assert business and all(not (set(a["tables"]) & mirror_tables) for a in business)
    mirror_areas = [a for a in g.data["areas"] if a.get("mirror")]
    assert len(mirror_areas) == 1 and set(mirror_areas[0]["tables"]) == mirror_tables and mirror_areas[0]["mirror_of"] == "shop"
    ids = {a["id"] for a in mirror_areas}
    assert all(l["a"] not in ids and l["b"] not in ids for l in g.data["area_links"])
    assert Q.find_paths(g, "shop.events", "shop.legacy", 4, 0.4, 5) == []
    via = Q.find_paths(g, "shop.events", "shop.legacy", 4, 0.4, 5, include_mirrors=True)
    assert via and via[0]["tables"] == ["shop.events", "shop_copy.orders", "shop_copy.customers", "shop.legacy"]
    # an explicit mirror endpoint is still allowed
    assert Q.find_paths(g, "shop_copy.orders", "shop_copy.customers", 4, 0.4, 3)


def test_cli_include_mirrors(tmp_path, capsys):
    run_pipeline(twins(), tmp_path, verbose=False)
    assert main(["path", "shop.events", "shop.legacy", "--dir", str(tmp_path), "--min-confidence", "0.4", "--json"]) == 0
    plain = json.loads(capsys.readouterr().out)
    assert main(["path", "shop.events", "shop.legacy", "--dir", str(tmp_path), "--min-confidence", "0.4", "--json", "--include-mirrors"]) == 0
    withm = json.loads(capsys.readouterr().out)
    assert plain["paths"] == []
    assert any(t.startswith("shop_copy.") for p in withm["paths"] for t in p["tables"])
    assert main(["areas", "--dir", str(tmp_path), "--json"]) == 0
    assert not any(r.get("mirror") for r in json.loads(capsys.readouterr().out))
    assert main(["areas", "--dir", str(tmp_path), "--json", "--include-mirrors"]) == 0
    assert any(r.get("mirror") for r in json.loads(capsys.readouterr().out))
    assert main(["area", "shop_copy.orders", "--dir", str(tmp_path)]) == 2
    capsys.readouterr()
    assert main(["area", "shop_copy.orders", "--dir", str(tmp_path), "--include-mirrors"]) == 0


def test_a_copy_may_add_or_drop_columns():
    from keygraph.graph import build_graph
    s = twins()
    s.tables["shop_copy.orders"].columns.append(Column("loaded_at", "datetime"))   # the copy adds an ETL column
    s.tables["shop_copy.products"].columns.pop()                                   # and drops one
    assert detect_mirrors(build_graph(s)) == {"shop_copy": "shop"}


def test_context_file_overrides_the_direction(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("mirrors:\n  shop: shop_copy\n", encoding="utf-8")   # shop is the copy, shop_copy the original
    run_pipeline(twins(), tmp_path / "o", context=[str(f)], verbose=False)
    assert Graph.load(tmp_path / "o" / "graph.json").data["mirrors"] == {"shop": "shop_copy"}
    f.write_text("mirrors:\n  shop_copy: false\n", encoding="utf-8")   # never treat shop_copy as a mirror
    run_pipeline(twins(), tmp_path / "o", context=[str(f)], verbose=False)
    assert Graph.load(tmp_path / "o" / "graph.json").data["mirrors"] == {}


def indexed_original_and_bigger_copy():
    """'shop' (5 tables, foreign-key indexes) and 'acopy' (the same 5 tables plus one extra, primary keys only).
    The copy is larger and sorts first by name, so size and name alone would pick the wrong original."""
    s = Schema()
    for t, cols in SHOP.items():
        idx = list(PK) + [Index(f"ix_{t}_{c}", [c]) for c in cols if c.endswith("_id")]
        s.add(Table("shop", t, [Column(c, "int") for c in cols], idx))
        s.add(Table("acopy", t, [Column(c, "int") for c in cols], list(PK)))
    s.add(Table("acopy", "load_log", [Column("id", "int"), Column("loaded_at", "datetime")], list(PK)))
    return s


def test_original_is_the_schema_with_keys_and_relations_not_the_larger_one():
    from keygraph.graph import build_graph
    warns = []
    assert detect_mirrors(build_graph(indexed_original_and_bigger_copy()), warnings=warns) == {"acopy": "shop"}
    assert not warns  # decided by keys and relations: not a close call


def test_close_guess_and_disagreeing_force_warn():
    from keygraph.graph import build_graph
    plain = Schema()  # two identical schemas with primary keys only: nothing tells the source from the copy
    for sch in ("left", "right"):
        for t, cols in SHOP.items():
            plain.add(Table(sch, t, [Column(c, "int") for c in cols], list(PK)))
    warns = []
    assert detect_mirrors(build_graph(plain), warnings=warns) == {"right": "left"}
    assert any(w.startswith("mirror guess: right copies left") and "mirrors.yaml" in w for w in warns)
    warns = []
    g = build_graph(indexed_original_and_bigger_copy())
    assert detect_mirrors(g, force={"shop": "acopy"}, warnings=warns) == {"shop": "acopy"}
    assert any("disagrees" in w and "acopy copies shop" in w for w in warns)
    warns = []
    detect_mirrors(g, force={"nope": "shop"}, warnings=warns)
    assert any("nope" in w and "unknown schema" in w for w in warns)
