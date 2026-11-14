import sqlite3

import pytest
from test_example_shop import _build

from keygraph.context import ContextDB
from keygraph.graph import Graph
from keygraph.index import plan as P


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    out = tmp_path_factory.mktemp("idxshop")
    _build(out, app=True)
    return out


def _load(out):
    return Graph.load(out / "graph.json"), ContextDB(out / "keygraph.db", readonly=True)


def test_rank_is_stable_across_calls_and_builds(shop, tmp_path):
    g, ctx = _load(shop)
    a, b = P.rank_tables(g, ctx), P.rank_tables(g, ctx)
    assert a == b and len(a) == len(g.nodes) - len(P.skipped_tables(g)) and P.skipped_tables(g)
    out2 = tmp_path / "again"
    _build(out2, app=True)
    g2, ctx2 = _load(out2)
    assert P.rank_tables(g2, ctx2) == a


def test_rank_orders_by_score_then_table_id(shop):
    g, ctx = _load(shop)
    r = P.rank_tables(g, ctx)
    assert [(-x["score"], x["table"]) for x in r] == sorted((-x["score"], x["table"]) for x in r)
    assert r[0]["score"] > r[-1]["score"]
    assert set(r[0]["parts"]) == {"hub", "queries", "code", "context", "area", "rows", "degree", "presence"}


def test_rank_counts_code_and_query_hits(shop):
    g, ctx = _load(shop)
    r = {x["table"]: x for x in P.rank_tables(g, ctx)}
    assert any(x["parts"]["code"] > 0 for x in r.values())
    assert any(x["parts"]["queries"] > 0 for x in r.values())
    assert any(x["parts"]["context"] > 0 for x in r.values())


def test_ties_break_by_table_id(tmp_path):
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    for n in ("zeta", "alpha", "mid"):
        s.add(Table("app", n, [Column("id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    g = build_graph(s)
    with ContextDB(tmp_path / "k.db", g) as ctx:
        assert [x["table"] for x in P.rank_tables(g, ctx)] == ["app.alpha", "app.mid", "app.zeta"]


def test_units_cover_every_table_once_with_stable_ids(shop):
    g, ctx = _load(shop)
    ranked = P.rank_tables(g, ctx)
    units = P.make_units(g, ranked)
    assert units == P.make_units(g, ranked)
    flat = [t for u in units for t in u["tables"]]
    assert sorted(flat) == sorted(set(g.nodes) - P.skipped_tables(g)) and len(flat) == len(set(flat))
    assert all(1 <= len(u["tables"]) <= 5 for u in units)
    assert any(len(u["tables"]) > 1 for u in units)
    assert [u["rank"] for u in units] == list(range(len(units)))
    assert len({u["unit_id"] for u in units}) == len(units)
    for u in units:
        assert u["unit_id"] == P.unit_id(reversed(u["tables"])) and len(u["unit_id"]) == 12


def test_units_respect_max_tables(shop):
    g, ctx = _load(shop)
    ranked = P.rank_tables(g, ctx)
    assert all(len(u["tables"]) == 1 for u in P.make_units(g, ranked, max_tables=1))
    assert all(len(u["tables"]) <= 5 for u in P.make_units(g, ranked, max_tables=99))


def test_unit_members_are_related(shop):
    g, ctx = _load(shop)
    for u in P.make_units(g, P.rank_tables(g, ctx)):
        if len(u["tables"]) > 1:
            linked = {frozenset((e["from"], e["to"])) for e in g.edges}
            t = u["tables"]
            assert all(any(frozenset((a, b)) in linked for b in t if b != a) for a in t)


def test_old_keygraph_db_opens_readonly_without_new_tables(shop, tmp_path):
    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE concepts(name TEXT PRIMARY KEY, definition TEXT, domain TEXT, owner TEXT, synonyms TEXT, "
                "source TEXT, verified INTEGER, updated_at TEXT)")
    con.commit()
    con.close()
    before = old.read_bytes()
    g = Graph.load(shop / "graph.json")
    with ContextDB(old, g, readonly=True) as ctx:
        for t in ("index_jobs", "index_units", "fact_provenance", "index_relations", "performance_notes"):
            assert ctx.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
        assert len(P.rank_tables(g, ctx)) == len(g.nodes) - len(P.skipped_tables(g))
    assert old.read_bytes() == before
    names = {r[0] for r in sqlite3.connect(old).execute("SELECT name FROM sqlite_master")}
    assert "index_jobs" not in names


def _schema(spec: dict):
    """spec: table -> list of referenced tables (declared FKs on <ref>_id columns); schema app."""
    from keygraph.graph import build_graph
    from keygraph.model import Column, ForeignKey, Index, Schema, Table
    s = Schema()
    for name, refs in spec.items():
        cols = [Column("id", "int")] + [Column(f"{r}_id", "int") for r in refs]
        fks = [ForeignKey(f"fk_{name}_{r}", [f"{r}_id"], "app", r, ["id"]) for r in refs]
        s.add(Table("app", name, cols, [Index("PRIMARY", ["id"], True, True)], fks))
    return build_graph(s)


def _ctx(tmp_path, g):
    ctx = ContextDB(tmp_path / "k.db", g)
    ctx.index_graph(g)
    return ctx


def test_hub_signal_comes_from_incoming_edges(tmp_path):
    g = _schema({"core": [], "a": ["core"], "b": ["core"], "c": ["core", "a"], "lonely": []})
    r = P.rank_tables(g, _ctx(tmp_path, g))
    assert r[0]["table"] == "app.core" and r[0]["parts"]["hub"] == 1.0
    by = {x["table"]: x["parts"]["hub"] for x in r}
    assert by["app.a"] == 0.333333 and by["app.lonely"] == 0.0 and by["app.c"] == 0.0


STRONG = ("hub", "queries", "code", "context", "area")


def test_each_signal_reads_its_own_source(tmp_path):
    g = _schema({"t1": [], "t2": [], "t3": [], "t4": [], "t5": []})
    ctx = _ctx(tmp_path, g)
    db = ctx.db
    db.execute("CREATE TABLE IF NOT EXISTS query_refs(fp TEXT, table_id TEXT, column_name TEXT, role TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS code_refs(repo TEXT, path TEXT, line INT, kind TEXT, table_id TEXT, column_name TEXT)")
    db.executemany("INSERT INTO query_refs VALUES(?,?,?,?)", [("f1", "app.t1", "id", "x"), ("f2", "app.t1", "id", "x")])
    db.executemany("INSERT INTO code_refs VALUES('r','p',?,'use','app.t2','id')", [(1,), (2,), (3,)])
    db.execute("INSERT INTO concept_links VALUES('c','app.t3','','attribute','','human',1,'')")
    db.execute("INSERT INTO table_notes(table_id, source, verified) VALUES('app.t4','llm',0)")
    ctx.db.commit()
    r = {x["table"]: x["parts"] for x in P.rank_tables(g, ctx)}
    assert {k: v for k, v in r["app.t1"].items() if k in STRONG} == {"hub": 0.0, "queries": 1.0, "code": 0.0, "context": 0.0, "area": 0.0}
    assert {k: v for k, v in r["app.t2"].items() if k in STRONG} == {"hub": 0.0, "queries": 0.0, "code": 1.0, "context": 0.0, "area": 0.0}
    assert {k: v for k, v in r["app.t3"].items() if k in STRONG} == {"hub": 0.0, "queries": 0.0, "code": 0.0, "context": 1.0, "area": 0.0}
    assert r["app.t4"]["context"] == 0.0 and r["app.t5"]["context"] == 0.0  # an llm-source note is not a mention


def test_area_signal_counts_edges_inside_an_area(tmp_path):
    g = _schema({"a": [], "b": ["a"], "c": ["a"], "x": []})
    g.data["areas"] = [{"id": "ar", "name": "A", "tables": ["app.a", "app.b", "app.c"]}]
    r = {x["table"]: x["parts"]["area"] for x in P.rank_tables(g, _ctx(tmp_path, g))}
    assert r["app.a"] == 1.0 and r["app.b"] == 0.5 and r["app.x"] == 0.0


def test_mirror_and_copy_tables_are_not_planned(tmp_path):
    g = _schema({"a": [], "b": ["a"], "m": ["a"], "cp": []})
    g.nodes["app.m"]["mirror_of"] = "app"
    ranked = P.rank_tables(g, _ctx(tmp_path, g))
    assert "app.m" not in {x["table"] for x in ranked}
    units = P.make_units(g, ranked)
    assert sorted(t for u in units for t in u["tables"]) == ["app.a", "app.b", "app.cp"]
    assert P.skipped_tables(g) == {"app.m"}


def test_copy_schema_tables_are_not_planned(tmp_path):
    from keygraph.graph import build_graph
    from keygraph.model import Column, Index, Schema, Table
    s = Schema()
    for sch in ("app", "app_copy"):
        s.add(Table(sch, "t", [Column("id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    g = build_graph(s)
    g.data["copy_schemas"] = ["app_copy"]
    assert [x["table"] for x in P.rank_tables(g, _ctx(tmp_path, g))] == ["app.t"]


def test_empty_graph(tmp_path):
    from keygraph.graph import build_graph
    from keygraph.model import Schema
    g = build_graph(Schema())
    assert P.rank_tables(g, _ctx(tmp_path, g)) == [] and P.make_units(g, []) == []


def test_component_larger_than_five_splits_into_units_of_five_or_less(tmp_path):
    g = _schema({"root": [], **{f"leaf{i}": ["root"] for i in range(8)}})
    ranked = P.rank_tables(g, _ctx(tmp_path, g))
    units = P.make_units(g, ranked)
    assert all(1 <= len(u["tables"]) <= 5 for u in units) and len(units) >= 2
    assert sorted(t for u in units for t in u["tables"]) == sorted(g.nodes)
    assert max(len(u["tables"]) for u in units) == 5


# --- families and data presence (ENG-106) -----------------------------------------------------------------------

def _shape(name: str, rows=None, extra=(), cols=("id", "customer_ref", "total", "placed_at")):
    from keygraph.model import Column, Index, Table
    c = [Column(n, "int" if n in ("id", "total") else "varchar(20)") for n in (*cols, *extra)]
    return Table("dw", name, c, [Index("PRIMARY", ["id"], True, True)], [], {"rows_estimate": rows} if rows is not None else {})


def _warehouse(*tables):
    from keygraph.graph import build_graph
    from keygraph.model import Schema
    s = Schema()
    for t in tables:
        s.add(t)
    return build_graph(s)


def _profile(ctx, table, col, **p):
    import json
    ctx.db.execute("INSERT OR REPLACE INTO column_profile VALUES(?,?,?,?,?,?)",
                   (table, col, json.dumps({"sample_size": 100, **p}), "", 100, "limit"))


def test_same_shape_tables_form_a_family_with_one_representative(tmp_path):
    g = _warehouse(_shape("t_orders", 5000), _shape("t_orders_bak", 5000), _shape("x_orders_2", 3000),
                   _shape("customers", 40, cols=("id", "full_name", "tier")))
    ctx = _ctx(tmp_path, g)
    fam = P.families(g, ctx)
    assert fam == {"dw.t_orders": ["dw.t_orders_bak", "dw.x_orders_2"]}  # most rows; a tie goes to the table id order
    ranked = [r["table"] for r in P.rank_tables(g, ctx)]
    assert "dw.t_orders" in ranked and "dw.t_orders_bak" not in ranked and "dw.x_orders_2" not in ranked
    assert "dw.customers" in ranked
    assert P.families(g, ctx) == fam
    units = P.make_units(g, P.rank_tables(g, ctx))
    assert sorted(t for u in units for t in u["tables"]) == ["dw.customers", "dw.t_orders"]


def test_family_signature_ignores_column_order_but_not_names_or_types(tmp_path):
    a = _shape("a_t", 10)
    b = _shape("b_t", 10, cols=("total", "id", "placed_at", "customer_ref"))
    c = _shape("c_t", 10, cols=("id", "customer_ref", "total", "placed_on"))
    g = _warehouse(a, b, c)
    assert P.families(g, _ctx(tmp_path, g)) == {"dw.a_t": ["dw.b_t"]}


def test_two_column_shapes_are_too_generic_for_a_family(tmp_path):
    g = _warehouse(_shape("x", 5, cols=("id", "name")), _shape("y", 5, cols=("id", "name")))
    assert P.families(g, _ctx(tmp_path, g)) == {}


def test_family_representative_falls_back_to_relations_then_name(tmp_path):
    from keygraph.graph import build_graph
    from keygraph.model import Column, ForeignKey, Index, Schema, Table
    s = Schema()
    for n in ("p", "q"):
        s.add(Table("dw", n, [Column("id", "int"), Column("a", "int"), Column("b", "int")], [Index("PRIMARY", ["id"], True, True)]))
    s.add(Table("dw", "kid", [Column("id", "int"), Column("q_id", "int")], [Index("PRIMARY", ["id"], True, True)],
                [ForeignKey("fk", ["q_id"], "dw", "q", ["id"])]))
    g = build_graph(s)
    assert P.families(g, _ctx(tmp_path, g)) == {"dw.q": ["dw.p"]}  # no row counts: q has a relation, p has none


def test_profiled_key_ranges_that_do_not_overlap_split_a_family(tmp_path):
    g = _warehouse(_shape("o1", 100), _shape("o2", 100), _shape("o3", 100))
    ctx = _ctx(tmp_path, g)
    _profile(ctx, "dw.o1", "id", numeric={"min": 1, "max": 100})
    _profile(ctx, "dw.o2", "id", numeric={"min": 50, "max": 150})  # overlaps o1
    _profile(ctx, "dw.o3", "id", numeric={"min": 9000, "max": 9999})  # a different data set with the same shape
    assert P.families(g, ctx) == {"dw.o1": ["dw.o2"]}
    ranked = {r["table"] for r in P.rank_tables(g, ctx)}
    assert ranked == {"dw.o1", "dw.o3"}


def test_shared_top_values_count_as_overlap(tmp_path):
    g = _warehouse(_shape("o1", 100), _shape("o2", 100))
    ctx = _ctx(tmp_path, g)
    _profile(ctx, "dw.o1", "customer_ref", top_values=[["a", 3], ["b", 2]], values_complete=True)
    _profile(ctx, "dw.o2", "customer_ref", top_values=[["b", 3], ["c", 2]], values_complete=True)
    assert P.families(g, ctx) == {"dw.o1": ["dw.o2"]}
    _profile(ctx, "dw.o2", "customer_ref", top_values=[["x", 3], ["y", 2]], values_complete=True)
    assert P.families(g, ctx) == {}


def test_empty_and_tiny_tables_rank_lower_and_row_count_breaks_ties(tmp_path):
    g = _warehouse(_shape("big", 1_000_000, cols=("id", "a", "b")), _shape("mid", 5_000, cols=("id", "c", "d")),
                   _shape("tiny", 4, cols=("id", "e", "f")), _shape("empty", 0, cols=("id", "g", "h")),
                   _shape("unknown", None, cols=("id", "i", "j")))
    ranked = P.rank_tables(g, _ctx(tmp_path, g))
    r = {x["table"]: x for x in ranked}
    order = [x["table"] for x in ranked]
    assert order[:2] == ["dw.big", "dw.mid"] and order[-1] == "dw.empty"
    assert order.index("dw.tiny") > order.index("dw.unknown") > order.index("dw.mid")
    assert r["dw.big"]["parts"]["rows"] == 1.0 and 0 < r["dw.mid"]["parts"]["rows"] < 1.0
    assert r["dw.empty"]["parts"]["presence"] < r["dw.tiny"]["parts"]["presence"] < 0 == r["dw.unknown"]["parts"]["presence"]


def test_relation_degree_counts_both_directions(tmp_path):
    g = _schema({"core": [], "a": ["core"], "b": ["core", "a"], "c": ["a"], "lonely": []})
    r = {x["table"]: x["parts"]["degree"] for x in P.rank_tables(g, _ctx(tmp_path, g))}
    assert r["app.a"] == 1.0 and r["app.lonely"] == 0.0 and 0 < r["app.c"] < 1.0


def test_query_log_and_code_signals_still_dominate_data_presence(tmp_path):
    g = _warehouse(_shape("huge", 10_000_000, cols=("id", "a", "b")), _shape("asked", 0, cols=("id", "c", "d")))
    ctx = _ctx(tmp_path, g)
    ctx.db.execute("CREATE TABLE IF NOT EXISTS query_refs(fp TEXT, table_id TEXT, column_name TEXT, role TEXT)")
    ctx.db.execute("INSERT INTO query_refs VALUES('f1','dw.asked','id','x')")
    assert next(x["table"] for x in P.rank_tables(g, ctx)) == "dw.asked"


def test_plan_is_the_same_for_the_same_map(tmp_path):
    tables = [_shape(n, r) for n, r in (("a1", 50), ("a2", 50), ("a3", 70))] + [_shape("z", 3, cols=("id", "x", "y"))]
    g1, g2 = _warehouse(*tables), _warehouse(*reversed(tables))
    c1, c2 = _ctx(tmp_path, g1), ContextDB(tmp_path / "k2.db", g2)
    c2.index_graph(g2)
    assert P.rank_tables(g1, c1) == P.rank_tables(g2, c2) and P.families(g1, c1) == P.families(g2, c2)
    assert P.make_units(g1, P.rank_tables(g1, c1)) == P.make_units(g2, P.rank_tables(g2, c2))


def test_family_representative_prefers_the_table_people_use_over_more_rows(tmp_path):
    g = _warehouse(_shape("old_copy", 9000), _shape("live", 100))
    ctx = _ctx(tmp_path, g)
    ctx.db.execute("CREATE TABLE IF NOT EXISTS query_refs(fp TEXT, table_id TEXT, column_name TEXT, role TEXT)")
    ctx.db.execute("INSERT INTO query_refs VALUES('f1','dw.live','id','x')")
    assert P.families(g, ctx) == {"dw.live": ["dw.old_copy"]}


def test_family_report_says_what_chose_the_representative(tmp_path):
    g = _warehouse(_shape("a", 10), _shape("b", 10), _shape("c", 9, cols=("id", "x", "y")), _shape("d", 9, cols=("id", "x", "y")),
                   _shape("e", None, cols=("id", "p", "q")), _shape("f", None, cols=("id", "p", "q")))
    fams, basis = P.family_report(g, _ctx(tmp_path, g))
    assert fams == {"dw.a": ["dw.b"], "dw.c": ["dw.d"], "dw.e": ["dw.f"]}
    assert basis == {"dw.a": "name", "dw.c": "name", "dw.e": "name"}  # every pair is a tie but for the id
    g = _warehouse(_shape("a", 10), _shape("b", 20))
    assert P.family_report(g, _ctx(tmp_path, g))[1] == {"dw.b": "rows"}
