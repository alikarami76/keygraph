import sqlite3

import pytest

from keygraph.context import ContextDB
from keygraph.graph import Graph
from keygraph.index import plan as P

from test_example_shop import _build


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
    assert a == b and len(a) == len(g.nodes)
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
    assert sorted(flat) == sorted(g.nodes) and len(flat) == len(set(flat))
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
        assert len(P.rank_tables(g, ctx)) == len(g.nodes)
    assert old.read_bytes() == before
    names = {r[0] for r in sqlite3.connect(old).execute("SELECT name FROM sqlite_master")}
    assert "index_jobs" not in names
