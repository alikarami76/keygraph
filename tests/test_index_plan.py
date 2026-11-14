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
    assert set(r[0]["parts"]) == {"hub", "queries", "code", "context", "area"}


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
    assert r["app.t1"] == {"hub": 0.0, "queries": 1.0, "code": 0.0, "context": 0.0, "area": 0.0}
    assert r["app.t2"] == {"hub": 0.0, "queries": 0.0, "code": 1.0, "context": 0.0, "area": 0.0}
    assert r["app.t3"] == {"hub": 0.0, "queries": 0.0, "code": 0.0, "context": 1.0, "area": 0.0}
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
