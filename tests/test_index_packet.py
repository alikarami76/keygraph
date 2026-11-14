import json
import sqlite3

import pytest
from test_example_shop import SHOP, _build

from keygraph.context import ContextDB
from keygraph.graph import Graph, build_graph
from keygraph.index import packet as K
from keygraph.index import plan as P
from keygraph.index import schema as S
from keygraph.model import Column, ForeignKey, Index, Schema, Table


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    out = tmp_path_factory.mktemp("pktshop")
    _build(out, profile=True, app=True)
    return out


def _load(out):
    return Graph.load(out / "graph.json"), ContextDB(out / "keygraph.db", readonly=True)


def test_packet_sections_for_orders(shop):
    g, ctx = _load(shop)
    p = K.build_packet(g, ctx, ["shop.orders", "shop.order_items"])
    assert p["unit_tables"] == ["shop.order_items", "shop.orders"] and [t["table"] for t in p["tables"]] == p["unit_tables"]
    orders = next(t for t in p["tables"] if t["table"] == "shop.orders")
    assert {"name", "type", "nullable"} <= set(orders["columns"][0]) and orders["primary_key"] == ["id"]
    assert p["candidate_relations"] and all(r["confidence"] >= 0.6 and r["evidence"] for r in p["candidate_relations"])
    assert p["neighbours"] and p["code_refs"] and p["queries"] and p["context"] and p["profiles"]
    assert all(n["table"] not in p["unit_tables"] for n in p["neighbours"])
    assert K.estimate(p) <= K.DEFAULT_CAP


def test_packet_is_byte_stable_across_calls_and_builds(shop, tmp_path):
    g, ctx = _load(shop)
    a = S.canonical(K.build_packet(g, ctx, ["shop.orders", "shop.customers"]))
    assert a == S.canonical(K.build_packet(g, ctx, ["shop.customers", "shop.orders"]))
    out2 = tmp_path / "again"
    _build(out2, profile=True, app=True)
    g2, ctx2 = _load(out2)
    b = S.canonical(K.build_packet(g2, ctx2, ["shop.orders", "shop.customers"]))
    assert a == b and "profiled_at" not in a
    ins, sch = S.instructions(), S.answer_schema()
    assert S.prompt_hash(ins, sch, json.loads(a)) == S.prompt_hash(ins, sch, json.loads(b))


def test_pii_columns_have_no_raw_values(shop):
    g, ctx = _load(shop)
    con = sqlite3.connect(SHOP / "shop.db")
    raw = [str(v) for col in ("email", "first_name", "last_name") for (v,) in con.execute(f"SELECT {col} FROM customers") if v]
    assert len(raw) > 100
    text = S.canonical(K.build_packet(g, ctx, ["shop.customers"]))
    assert not [v for v in raw if v in text]
    prof = K.build_packet(g, ctx, ["shop.customers"])["profiles"]
    assert prof["shop.customers.email"]["pii"] is True and "top_values" not in prof["shop.customers.email"]
    assert prof["shop.customers.email"]["pattern"] == "email"
    assert "top_values" in prof["shop.customers.id"]


def test_pii_profile_with_planted_values_is_dropped(shop, tmp_path):
    """Defence in depth: even a stored profile that holds raw values for a PII column gives counts and pattern only."""
    import shutil
    d = tmp_path / "copy"
    shutil.copytree(shop, d)
    con = sqlite3.connect(d / "keygraph.db")
    bad = {"sample_size": 5, "null_rate": 0, "pattern": "email", "top_values": [["leak@example.com", 1]],
           "numeric": {"min": 123456789}, "dates": {"min": "1990-01-01"}, "profiled_at": "x"}
    con.execute("UPDATE column_profile SET profile=? WHERE table_id='shop.customers' AND col='email'", (json.dumps(bad),))
    con.execute("INSERT OR REPLACE INTO column_profile VALUES('shop.customers','national_id',?,'',5,'')",
                (json.dumps({"sample_size": 5, "top_values": [["0012345678", 1]]}),))
    con.commit()
    con.close()
    g, ctx = _load(d)
    text = S.canonical(K.build_packet(g, ctx, ["shop.customers"]))
    assert "leak@example.com" not in text and "123456789" not in text and "1990-01-01" not in text
    assert '"sample_size":5' in text


def test_secrets_are_masked_in_every_text_field(shop, tmp_path):
    import shutil
    d = tmp_path / "copy"
    shutil.copytree(shop, d)
    con = sqlite3.connect(d / "keygraph.db")
    con.execute("UPDATE code_refs SET snippet='conn = \"postgres://admin:s3cretpw@db/shop\"' WHERE table_id='shop.orders'")
    con.execute("UPDATE query_fingerprints SET normalized_sql='select 1 from shop.orders where e = ''ann@example.com'''")
    con.execute("UPDATE concepts SET definition='mail ann@example.com token=abcdefghijklmnopqrstuvwxyz0123456789'")
    con.commit()
    con.close()
    g, ctx = _load(d)
    text = S.canonical(K.build_packet(g, ctx, ["shop.orders", "shop.order_items"]))
    for leak in ("s3cretpw", "ann@example.com", "abcdefghijklmnopqrstuvwxyz0123456789"):
        assert leak not in text


def _wide(n=400):
    s = Schema()
    s.add(Table("app", "parent", [Column("id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    cols = [Column("id", "int")] + [Column(f"col_{i}", "varchar(50)", comment=f"comment {i} " + "word " * 30) for i in range(n)]
    cols.append(Column("parent_id", "int"))
    s.add(Table("app", "wide", cols, [Index("PRIMARY", ["id"], True, True)], [ForeignKey("fk", ["parent_id"], "app", "parent", ["id"])]))
    return build_graph(s)


def test_cap_is_respected_on_a_wide_table(tmp_path):
    g = _wide()
    with ContextDB(tmp_path / "k.db", g) as ctx:
        p = K.build_packet(g, ctx, ["app.wide", "app.parent"])
        assert K.estimate(p) <= K.DEFAULT_CAP and p["trimmed"] == ["column_details"]
        assert p["candidate_relations"]  # comments go before relations
        huge = K.build_packet(_wide(2000), ctx, ["app.wide", "app.parent"])
        wide = next(t for t in huge["tables"] if t["table"] == "app.wide")
        assert K.estimate(huge) <= K.DEFAULT_CAP and wide["columns_omitted"] > 0 and "id" in [c["name"] for c in wide["columns"]]
        assert huge["trimmed"][-1] == "columns"
        small = K.build_packet(g, ctx, ["app.wide"], cap=1500)
        assert K.estimate(small) <= 1500
        big = K.build_packet(g, ctx, ["app.parent"])
        assert big["trimmed"] == []


def test_sections_trim_in_fixed_order(shop):
    g, ctx = _load(shop)
    full = K.build_packet(g, ctx, ["shop.orders", "shop.order_items"], cap=100000)
    assert full["trimmed"] == []
    size = K.estimate(full)
    mid = K.build_packet(g, ctx, ["shop.orders", "shop.order_items"], cap=size - 150)
    assert mid["trimmed"][0] == "context" and K.estimate(mid) <= size - 150
    assert len(mid["tables"]) == 2 and len(mid["profiles"]) == len(full["profiles"])
    assert mid == K.build_packet(g, ctx, ["shop.orders", "shop.order_items"], cap=size - 150)


def test_long_unbroken_comment_is_bounded_before_masking(tmp_path):
    import time
    s = Schema()
    s.add(Table("app", "t", [Column("id", "int", comment="a" * 200000)], [Index("PRIMARY", ["id"], True, True)]))
    g = build_graph(s)
    with ContextDB(tmp_path / "k.db", g) as ctx:
        t0 = time.time()
        p = K.build_packet(g, ctx, ["app.t"])
        assert time.time() - t0 < 2 and K.estimate(p) <= K.DEFAULT_CAP


def test_old_db_without_code_or_query_tables(shop, tmp_path):
    g = Graph.load(shop / "graph.json")
    with ContextDB(tmp_path / "new.db", g) as ctx:
        p = K.build_packet(g, ctx, ["shop.orders"])
    assert p["code_refs"] == [] and p["queries"] == [] and p["profiles"] == {}


def test_every_planned_unit_fits_the_cap(shop):
    g, ctx = _load(shop)
    for u in P.make_units(g, P.rank_tables(g, ctx)):
        assert K.estimate(K.build_packet(g, ctx, u["tables"])) <= K.DEFAULT_CAP
