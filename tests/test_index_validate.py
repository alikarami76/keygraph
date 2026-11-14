import json

import pytest
from test_example_shop import SHOP

from keygraph.cli import main
from keygraph.context import ContextDB
from keygraph.graph import Graph
from keygraph.index import validate as V

UNIT = {"unit_tables": ["billing.payments", "shop.orders"]}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out, d = tmp_path_factory.mktemp("valshop"), SHOP / "dumps"
    assert main(["load", "--dir", str(out), "--columns", str(d / "columns.json"), "--statistics", str(d / "statistics.json"),
                 "--key-columns", str(d / "key_columns.json"), "--tables", str(d / "tables.json")]) == 0
    return out


@pytest.fixture()
def env(built):
    g = Graph.load(built / "graph.json")
    ctx = ContextDB(built / "keygraph.db")
    yield g, ctx
    ctx.db.rollback()
    ctx.close()


def _rel(ft="billing.payments", fc=("order_id",), tt="shop.orders", tc=("id",), **kw):
    return {"from_table": ft, "from_columns": list(fc), "to_table": tt, "to_columns": list(tc), "reason": "values match",
            "confidence": 0.7, **kw}


def _col(**kw):
    return {"table": "shop.orders", "column": "status", "meaning": "Order state", "reason": "r", "confidence": 0.8, **kw}


def _tab(**kw):
    return {"table": "shop.orders", "purpose": "One checkout", "reason": "r", "confidence": 0.8, **kw}


def _check(env, **lists):
    g, ctx = env
    return V.validate(lists, g, ctx, UNIT)


def _only_reason(res, kind="relations"):
    assert not res["accepted"], res
    (r,) = res["rejected"]
    assert r["kind"] == kind
    return r["reason"]


def test_good_answer_is_accepted_in_full(env):
    res = _check(env, relations=[_rel()], columns=[_col(codes={"1": "placed", "2": "paid"}, null_meaning="never null")],
                 tables=[_tab(grain="one row per order", grain_columns=["id"], default_filters=["status = 2"])])
    assert res["rejected"] == [] and res["errors"] == []
    assert [(a["kind"], a["index"]) for a in res["accepted"]] == [("relations", 0), ("columns", 0), ("tables", 0)]
    assert res["accepted"][0]["item"]["to_table"] == "shop.orders"


def test_unknown_column_is_rejected_with_its_reason(env):
    reason = _only_reason(_check(env, columns=[_col(column="state_code")]), "columns")
    assert "state_code" in reason and "shop.orders" in reason and "column" in reason
    assert "from_columns" in _only_reason(_check(env, relations=[_rel(fc=("nope",))]))
    assert "grain_columns" in _only_reason(_check(env, tables=[_tab(grain_columns=["nope"])]), "tables")


def test_unknown_table_and_table_outside_the_unit(env):
    assert "not a table in the map" in _only_reason(_check(env, columns=[_col(table="shop.nothing")]), "columns")
    assert "not a table in the map" in _only_reason(_check(env, columns=[_col(table="orders")]), "columns")
    assert "outside this unit" in _only_reason(_check(env, tables=[_tab(table="shop.customers")]), "tables")
    assert "not a table in the map" in _only_reason(_check(env, relations=[_rel(tt="shop.nothing")]))
    # a relation may reach any graph table, but one end must be in the unit
    assert not _check(env, relations=[_rel(tt="shop.products")])["rejected"]
    assert "no table of this unit" in _only_reason(_check(env, relations=[_rel(ft="shop.customers", fc=("id",), tt="shop.products")]))


def test_email_in_a_meaning_is_rejected_without_echoing_it(env):
    res = _check(env, columns=[_col(meaning="Contact ann.b@example.com for help")])
    reason = _only_reason(res, "columns")
    assert "personal value (email)" in reason and "meaning" in reason and "example.com" not in json.dumps(res)


@pytest.mark.parametrize("text", ["call 09121234567", "card 4111 1111 1111 1111", "owner 0012345678", "password=hunter2",
                                  "set PGPASSWORD hunter2", "mysql -u root -phunter2", "api_key: abc123"])
def test_personal_values_and_secrets_are_rejected_in_any_field(env, text):
    for item, kind in ((_col(meaning=text), "columns"), (_tab(pitfalls=["fine", text]), "tables"),
                       (_col(codes={"1": text}), "columns"), (_rel(reason=text), "relations")):
        reason = _only_reason(_check(env, **{kind: [item]}), kind)
        assert "personal value" in reason or "secret" in reason


def test_pii_in_a_code_key_is_rejected(env):
    assert "codes" in _only_reason(_check(env, columns=[_col(codes={"09121234567": "x"})]), "columns")


def test_contradicting_or_repeating_a_declared_fk_is_rejected(env):
    g, ctx = env
    res = V.validate({"relations": [_rel(ft="shop.order_items", fc=("order_id",), tt="shop.products", tc=("id",))]}, g, ctx,
                     {"unit_tables": ["shop.order_items"]})
    reason = res["rejected"][0]["reason"]
    assert "contradicts declared foreign key" in reason and "shop.orders" in reason
    again = V.validate({"relations": [_rel(ft="shop.order_items", fc=("order_id",), tt="shop.orders", tc=("id",))]}, g, ctx,
                       {"unit_tables": ["shop.order_items"]})
    assert "already declared" in again["rejected"][0]["reason"]
    rev = V.validate({"relations": [_rel(ft="shop.orders", fc=("id",), tt="shop.order_items", tc=("order_id",))]}, g, ctx,
                     {"unit_tables": ["shop.orders"]})
    assert "already declared" in rev["rejected"][0]["reason"]


def test_a_relation_touching_a_confirmed_override_is_rejected(env):
    g, ctx = env
    edge = next(e for e in g.edges if e["from"] == "shop.orders" and e["from_columns"] == ["voucher_id"])
    old = edge["status"]
    try:
        edge["status"] = "confirmed"
        res = V.validate({"relations": [_rel(ft="shop.orders", fc=("voucher_id",), tt=edge["to"], tc=tuple(edge["to_columns"]))]},
                         g, ctx, UNIT)
        assert "user override" in res["rejected"][0]["reason"]
    finally:
        edge["status"] = old
    assert not V.validate({"relations": [_rel(ft="shop.orders", fc=("voucher_id",), tt=edge["to"], tc=tuple(edge["to_columns"]))]},
                          g, ctx, UNIT)["rejected"]


def test_confidence_outside_the_range_is_rejected_per_item(env):
    res = _check(env, relations=[_rel(confidence=1.5), _rel(confidence=-0.1), _rel(confidence=True), _rel(confidence=0.0)])
    assert [r["index"] for r in res["rejected"]] == [0, 1, 2] and [a["index"] for a in res["accepted"]] == [3]
    assert "confidence" in res["rejected"][0]["reason"] and "between 0 and 1" in res["rejected"][0]["reason"]


def test_malformed_codes_are_rejected(env):
    for codes in ({"": "empty code"}, {"x" * 41: "too long"}, {"1": "a", " 1": "b"}, {"1": ""}, {"1\n2": "control"}, {"1": 5}):
        res = _check(env, columns=[_col(codes=codes)])
        assert len(res["rejected"]) == 1, codes
        assert "codes" in res["rejected"][0]["reason"]
    assert _check(env, columns=[_col(codes=[1, 2])])["rejected"][0]["reason"].startswith("codes")


def test_over_length_text_is_rejected_with_the_limit(env):
    reason = _only_reason(_check(env, tables=[_tab(purpose="x" * 401)]), "tables")
    assert "purpose" in reason and "400" in reason


def test_missing_and_unknown_fields_are_item_level(env):
    res = _check(env, relations=[{"from_table": "billing.payments"}, dict(_rel(), extra=1)], columns=[_col()])
    assert len(res["rejected"]) == 2 and len(res["accepted"]) == 1 and res["errors"] == []
    assert any("missing" in r["reason"] for r in res["rejected"]) and any("unknown field" in r["reason"] for r in res["rejected"])


def test_structure_errors_reject_the_whole_answer(env):
    g, ctx = env
    for bad in ({"relations": [_rel()], "extra": []}, {"relations": "no"}, {}, [], "text"):
        res = V.validate(bad, g, ctx, UNIT)
        assert res["accepted"] == [] and res["errors"], bad
    res = V.validate({"relations": [_rel()]}, g, ctx, UNIT, tasks=["relations", "columns"])
    assert res["accepted"] == [] and any("columns" in e for e in res["errors"])


def test_a_verified_note_is_never_overwritten(env):
    g, ctx = env
    ctx.db.execute("INSERT OR REPLACE INTO table_notes(table_id, grain, summary, source, verified) VALUES(?,?,?,?,1)",
                   ("shop.orders", "one row per order", "", "ctx.yaml"))
    ctx.db.execute("INSERT OR REPLACE INTO column_semantics(table_id, col, meaning, codes, source, verified) VALUES(?,?,?,?,?,1)",
                   ("shop.orders", "status", "Order lifecycle state", json.dumps({"1": "placed", "2": "paid"}), "ctx.yaml"))
    bad = V.validate({"tables": [_tab(grain="one row per basket")], "columns": [_col(meaning="Something else"),
                                                                                   _col(codes={"1": "new"}),
                                                                                   _col(codes={"1": "placed", "2": "paid", "3": "x"})]},
                     g, ctx, UNIT)
    assert len(bad["rejected"]) == 4
    assert all("verified" in r["reason"] for r in bad["rejected"]) and not bad["accepted"]
    ok = V.validate({"tables": [_tab(grain=" One row per ORDER "), _tab(freshness="hourly", purpose="Another purpose")],
                     "columns": [_col(meaning="order lifecycle state", codes={"1": "Placed", "2": "paid"}), _col(meaning="Order lifecycle state", unit="n/a")]},
                    g, ctx, UNIT)
    assert not ok["rejected"] and len(ok["accepted"]) == 4  # same text, or a field the note leaves empty


def test_validation_works_without_a_context(env):
    g, _ = env
    assert not V.validate({"tables": [_tab(grain="g")]}, g, None, UNIT)["rejected"]


def test_concepts_and_metrics_tables_may_be_any_graph_table(env):
    res = _check(env, concepts=[{"name": "customer", "definition": "d", "tables": ["shop.customers"], "reason": "r", "confidence": 0.6},
                                {"name": "x", "definition": "d", "tables": ["shop.nope"], "reason": "r", "confidence": 0.6}],
                 metrics=[{"name": "m", "definition": "d", "expression": "SUM(o.total)", "tables": ["shop.orders"], "reason": "r",
                           "confidence": 0.6}])
    assert [a["kind"] for a in res["accepted"]] == ["concepts", "metrics"] and res["rejected"][0]["item"] == "concepts[1]"
