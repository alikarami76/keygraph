import json
import re
import shutil

import pytest
from test_example_shop import _build

from keygraph.cli import describe_result
from keygraph.context import ContextDB
from keygraph.graph import Graph
from keygraph.index import store as ST
from keygraph.query import find_paths

UNIT = {"unit_id": "u1", "tables": ["shop.orders"]}
META = ("job-0001", UNIT, "claude-x", "hash1")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("storeshop")
    _build(out)
    return out


@pytest.fixture()
def env(built, tmp_path):
    out = tmp_path / "out"
    shutil.copytree(built, out)
    g = Graph.load(out / "graph.json")
    ctx = ContextDB(out / "keygraph.db", g)
    yield out, g, ctx
    ctx.close()


def _store(env, answer, verdicts=(), unit=UNIT):
    _, g, ctx = env
    return ST.store_unit(ctx, g, "job-0001", unit, answer, "claude-x", "hash1", list(verdicts))


def _row(ctx, table, **keys):
    where = " AND ".join(f"{k}=?" for k in keys)
    return ctx.db.execute(f"SELECT * FROM {table} WHERE {where}", tuple(keys.values())).fetchone()


def _col(table="shop.carts", column="customer_id", **kw):
    return {"table": table, "column": column, "meaning": "Owner of the cart", "reason": "name and values", "confidence": 0.8, **kw}


def _rel(**kw):
    return {"from_table": "shop.returns", "from_columns": ["order_id"], "to_table": "shop.orders_shadow", "to_columns": ["id"],
            "reason": "same ids", "confidence": 0.99, **kw}


def _edge(g, frm, fc, to, tc):
    return next((e for e in g.edges if (e["from"], e["from_columns"], e["to"], e["to_columns"]) == (frm, fc, to, tc)), None)


def test_new_rows_are_llm_unverified_with_provenance(env):
    _, _, ctx = env
    res = _store(env, {"columns": [_col(codes={"1": "open"})], "tables": [{"table": "shop.carts", "purpose": "A basket", "grain": "one row per cart",
                 "pitfalls": ["abandoned carts stay"], "default_filters": ["status = 1"], "reason": "r", "confidence": 0.7}],
                 "concepts": [{"name": "basket", "definition": "Goods before checkout", "synonyms": ["cart"], "tables": ["shop.carts"],
                               "reason": "r", "confidence": 0.7}],
                 "metrics": [{"name": "open carts", "definition": "Carts not checked out", "expression": "COUNT(*)",
                              "tables": ["shop.carts"], "reason": "r", "confidence": 0.5}],
                 "performance": [{"table": "shop.carts", "hints": ["filter customer_id"], "reason": "index", "confidence": 0.6}]})
    assert res["kept"] == 0
    c = _row(ctx, "column_semantics", table_id="shop.carts", col="customer_id")
    assert (c["source"], c["verified"], c["meaning"], json.loads(c["codes"])) == ("llm", 0, "Owner of the cart", {"1": "open"})
    n = _row(ctx, "table_notes", table_id="shop.carts")
    assert (n["source"], n["summary"], n["grain"], n["pitfalls"]) == ("llm", "A basket", "one row per cart", "abandoned carts stay")
    assert json.loads(_row(ctx, "performance_notes", table_id="shop.carts")["notes"]) == {
        "default_filters": ["status = 1"], "hints": ["filter customer_id"]}
    assert _row(ctx, "concept_links", concept="basket", table_id="shop.carts")["source"] == "llm"
    assert _row(ctx, "metrics", name="open carts")["source"] == "llm"
    kinds = {r["kind"] for r in ctx.db.execute("SELECT kind FROM fact_provenance")}
    assert {"column", "table", "concept", "metric", "performance"} <= kinds
    p = _row(ctx, "fact_provenance", kind="column", ref="shop.carts.customer_id")
    assert (p["source"], p["model"], p["prompt_hash"], p["job_id"], p["unit_id"], p["verified"]) == ("llm", "claude-x", "hash1", "job-0001", "u1", 0)


def test_llm_never_overwrites_a_human_row(env):
    _, _, ctx = env
    before = dict(_row(ctx, "column_semantics", table_id="shop.orders", col="status"))
    nbefore = dict(_row(ctx, "table_notes", table_id="shop.orders"))
    res = _store(env, {"columns": [_col("shop.orders", "status", meaning="Something else", codes={"1": "x", "9": "y"})],
                       "tables": [{"table": "shop.orders", "purpose": "other", "grain": "one row per line", "reason": "r", "confidence": 0.9}]})
    assert dict(_row(ctx, "column_semantics", table_id="shop.orders", col="status")) == before
    assert dict(_row(ctx, "table_notes", table_id="shop.orders")) == {**nbefore, "summary": "other"}  # grain kept, empty summary filled
    assert res["kept"] >= 2
    assert _row(ctx, "fact_provenance", kind="column", ref="shop.orders.status") is None


def test_llm_fills_an_empty_field_of_a_human_row(env):
    _, _, ctx = env
    assert _row(ctx, "column_semantics", table_id="shop.orders", col="total")["null_meaning"] == ""
    res = _store(env, {"columns": [_col("shop.orders", "total", meaning="ignored", null_meaning="never null")]})
    r = _row(ctx, "column_semantics", table_id="shop.orders", col="total")
    assert (r["null_meaning"], r["source"], r["verified"]) == ("never null", "examples/shop/context.yaml", 1)
    assert r["meaning"] == "Order total including tax and delivery, after discounts"
    assert res["filled"] == 1
    p = _row(ctx, "fact_provenance", kind="column", ref="shop.orders.total")
    assert json.loads(p["probe"])["filled"]["fields"] == {"null_meaning": "never null"}


def test_llm_replaces_an_older_llm_row(env):
    _, _, ctx = env
    _store(env, {"columns": [_col(meaning="first", unit="id")]})
    _store(env, {"columns": [_col(meaning="second")]})
    (r,) = ctx.db.execute("SELECT * FROM column_semantics WHERE table_id='shop.carts' AND col='customer_id'").fetchall()
    assert (r["meaning"], r["unit"], r["source"]) == ("second", "", "llm")
    assert ctx.db.execute("SELECT COUNT(*) FROM fact_provenance WHERE ref='shop.carts.customer_id'").fetchone()[0] == 1


def test_failed_probe_drops_codes_but_keeps_meaning(env):
    _, _, ctx = env
    res = _store(env, {"columns": [_col(codes={"7": "ghost"})]}, [{"kind": "columns", "index": 0, "verified": False, "check": "codes_present", "rate": None, "n": 3}])
    r = _row(ctx, "column_semantics", table_id="shop.carts", col="customer_id")
    assert r["meaning"] and not json.loads(r["codes"] or "{}") and res["probe_failed"] == 1


def test_edge_confidence_caps(env):
    _, g, ctx = env
    _store(env, {"relations": [_rel(), _rel(from_table="shop.carts", from_columns=["id"], to_table="shop.orders_shadow", to_columns=["id"], confidence=0.4),
                               _rel(from_table="shop.cart_items", from_columns=["cart_id"], to_table="shop.orders_shadow", to_columns=["id"])]},
           [{"kind": "relations", "index": 0, "verified": None, "check": "none", "rate": None, "n": 0},
            {"kind": "relations", "index": 2, "verified": True, "check": "relation_overlap", "rate": 0.96, "n": 200}])
    res = ST.apply_index_relations(g, ctx)
    assert res["added"] == 3
    e = _edge(g, "shop.returns", ["order_id"], "shop.orders_shadow", ["id"])
    assert (e["confidence"], e["source"], e["kind"]) == (0.75, "llm", "inferred")
    assert e["evidence"] == ["llm: same ids"]
    assert _edge(g, "shop.carts", ["id"], "shop.orders_shadow", ["id"])["confidence"] == 0.4
    v = _edge(g, "shop.cart_items", ["cart_id"], "shop.orders_shadow", ["id"])
    assert v["confidence"] == 0.9 and any("probe passed" in x for x in v["evidence"])


def test_declared_fk_unchanged_and_equal_inferred_edge_only_raised(env):
    _, g, ctx = env
    decl = json.dumps(_edge(g, "shop.orders", ["customer_id"], "shop.customers", ["id"]), sort_keys=True)
    hi = json.dumps(_edge(g, "billing.payments", ["order_id"], "shop.orders", ["id"]), sort_keys=True)
    weak = _edge(g, "shop.orders", ["address_id"], "shop.customer_addresses", ["id"])
    assert weak["confidence"] < 0.6
    n_edges = len(g.edges)
    _store(env, {"relations": [
        _rel(from_table="shop.orders", from_columns=["customer_id"], to_table="shop.customers", to_columns=["id"]),
        _rel(from_table="shop.customers", from_columns=["id"], to_table="shop.orders", to_columns=["customer_id"]),
        _rel(from_table="billing.payments", from_columns=["order_id"], to_table="shop.orders", to_columns=["id"], confidence=0.6),
        _rel(from_table="shop.orders", from_columns=["address_id"], to_table="shop.customer_addresses", to_columns=["id"], confidence=0.7)]})
    res = ST.apply_index_relations(g, ctx)
    assert json.dumps(_edge(g, "shop.orders", ["customer_id"], "shop.customers", ["id"]), sort_keys=True) == decl
    assert res["unchanged"] == 2 and res["merged"] == 2 and res["added"] == 0 and len(g.edges) == n_edges
    p = _edge(g, "billing.payments", ["order_id"], "shop.orders", ["id"])
    assert p["confidence"] == 0.95 and p["evidence"][-1] == "llm: same ids"
    assert json.dumps({k: v for k, v in p.items() if k != "evidence"}, sort_keys=True) == json.dumps(
        {k: v for k, v in json.loads(hi).items() if k != "evidence"}, sort_keys=True)
    assert weak["confidence"] == 0.7  # raised to the capped llm confidence
    ST.strip_llm(g)  # undo restores the old value and evidence
    assert weak["confidence"] < 0.6 and not any(x.startswith("llm: ") for x in weak["evidence"])


def test_apply_is_idempotent_and_ordered(env):
    _, g, ctx = env
    _store(env, {"relations": [_rel(), _rel(from_table="shop.carts", from_columns=["id"], to_table="shop.orders_shadow", to_columns=["id"])]})
    ST.apply_index_relations(g, ctx)
    once = json.dumps(g.data["edges"], sort_keys=True)
    ST.apply_index_relations(g, ctx)
    assert json.dumps(g.data["edges"], sort_keys=True) == once
    ids = [e["id"] for e in g.edges]
    assert ids == sorted(ids) and len(ids) == len(set(ids))


def test_failed_probe_edge_is_a_rejected_candidate_and_absent_from_paths(env):
    _, g, ctx = env
    before = [find_paths(g, "shop.returns", "shop.orders_shadow", 4, 0.0, 5), find_paths(g, "shop.returns", "shop.carts", 4, 0.0, 5)]
    _store(env, {"relations": [_rel(confidence=0.95)]}, [{"kind": "relations", "index": 0, "verified": False, "check": "relation_overlap", "rate": 0.1, "n": 200}])
    res = ST.apply_index_relations(g, ctx)
    assert res["rejected"] == 1 and res["added"] == 0
    assert _edge(g, "shop.returns", ["order_id"], "shop.orders_shadow", ["id"]) is None
    assert before == [find_paths(g, "shop.returns", "shop.orders_shadow", 4, 0.0, 5), find_paths(g, "shop.returns", "shop.carts", 4, 0.0, 5)]
    r = ctx.db.execute("SELECT status FROM index_relations").fetchone()
    assert r["status"] == "rejected"


def test_planted_secret_is_masked_in_provenance(env):
    _, _, ctx = env
    _store(env, {"relations": [_rel(reason="joins on token=abc123SECRETxyz and 0912345678 a@b.com")],
                 "columns": [_col(reason="api_key=sk-ZZZsecret999")]})
    dump = json.dumps([dict(r) for r in ctx.db.execute("SELECT * FROM fact_provenance")]
                      + [dict(r) for r in ctx.db.execute("SELECT * FROM index_relations")])
    for bad in ("abc123SECRETxyz", "0912345678", "a@b.com", "sk-ZZZsecret999"):
        assert bad not in dump
    assert "***" in dump


def test_answer_file_has_sorted_keys_and_no_timestamp(env):
    out, _, _ = env
    p = ST.save_answer(out, UNIT, "job-0001", {"columns": [_col()]}, "claude-x", "hash1",
                       [{"kind": "columns", "index": 0, "verified": None, "check": "none", "rate": None, "n": 0}])
    assert p == out / "index" / "answers" / "u1.json"
    text = p.read_text()
    body = json.loads(text)
    assert list(body) == sorted(body)
    assert {"answer", "model", "prompt_hash", "schema_version", "verdicts", "tables"} <= set(body)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text) and "at" not in body and "time" not in text.lower()
    assert text == json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def test_accepted_answer_reindexes_verdicts():
    val = {"accepted": [{"kind": "columns", "index": 1, "item": {"a": 1}}, {"kind": "columns", "index": 3, "item": {"a": 2}}],
           "rejected": []}
    ans, vs = ST.accepted_answer(val, [{"kind": "columns", "index": 3, "verified": True}, {"kind": "columns", "index": 0, "verified": False}])
    assert ans == {"columns": [{"a": 1}, {"a": 2}]} and vs == [{"kind": "columns", "index": 1, "verified": True}]


def test_describe_shows_performance_notes_and_provenance_labels(env):
    out, g, ctx = env
    _store(env, {"columns": [_col("shop.orders", "placed_at", meaning="When the customer paid")],
                 "tables": [{"table": "shop.orders", "purpose": "x", "default_filters": ["status = 2"], "reason": "r", "confidence": 0.7}],
                 "performance": [{"table": "shop.orders", "slow_patterns": ["status without date"], "reason": "r", "confidence": 0.5},
                                 {"table": "shop.carts", "hints": ["h"], "reason": "r", "confidence": 0.5}],
                 "relations": [_rel(from_table="shop.orders", from_columns=["voucher_id"], to_table="billing.vouchers", to_columns=["id"]),
                               _rel(from_table="shop.orders", from_columns=["placed_at"], to_table="shop.orders_shadow", to_columns=["id"])]},
           [{"kind": "relations", "index": 1, "verified": True, "check": "relation_overlap", "rate": 1.0, "n": 9}])
    ST.apply_index_relations(g, ctx)
    ctx.db.commit()
    d = describe_result(out, g, "shop.orders", min_confidence=0)
    assert d["performance"]["slow_patterns"] == ["status without date"] and d["performance"]["default_filters"] == ["status = 2"]
    assert d["provenance"]["columns"]["status"] == "verified" and d["provenance"]["columns"]["placed_at"] == "llm"
    assert d["provenance"]["performance"] == "llm"
    labels = {(x["to"], x["to_columns"][0]): x["provenance"] for x in d["relations"] if x["from"] == "shop.orders"}
    assert labels[("shop.customers", "id")] == "declared"
    assert labels[("billing.vouchers", "id")] == "deterministic"
    assert labels[("shop.orders_shadow", "id")] == "llm, verified by probe"
    from keygraph.search import format_describe
    text = format_describe(d)
    assert "performance notes" in text and "llm, verified by probe" in text and "slow patterns: status without date" in text
    assert "When the customer paid" in text and "(llm)" in text
    brief = describe_result(out, g, "shop.orders", brief=True)
    assert brief["performance"]["default_filters"] == ["status = 2"]
