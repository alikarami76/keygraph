import json
import re
import shutil

import pytest
from test_example_shop import SHOP, _build

from keygraph.cli import describe_result, main
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
    assert weak["confidence"] < 0.6 and weak["evidence"][-1] == "llm: same ids" and "llm_boost" not in weak  # evidence only
    ST.strip_llm(g)  # undo removes the evidence
    assert weak["confidence"] < 0.6 and not any(x.startswith("llm: ") for x in weak["evidence"])


def test_unverified_claim_keeps_a_hidden_edge_hidden_and_verified_one_raises_to_0_9(env):
    _, g, ctx = env
    weak = _edge(g, "shop.orders", ["address_id"], "shop.customer_addresses", ["id"])
    mid = next(e for e in g.edges if e["kind"] == "inferred" and 0.6 <= e["confidence"] < 0.9
               and e["status"] == "inferred" and not g.nodes[e["from"]].get("mirror_of") and not g.nodes[e["to"]].get("mirror_of"))
    key = (mid["from"], mid["from_columns"], mid["to"], mid["to_columns"])
    _store(env, {"relations": [
        _rel(from_table="shop.orders", from_columns=["address_id"], to_table="shop.customer_addresses", to_columns=["id"], confidence=0.9),
        _rel(from_table=key[0], from_columns=key[1], to_table=key[2], to_columns=key[3], confidence=0.9)]},
        [{"kind": "relations", "index": 1, "verified": True, "check": "relation_overlap", "rate": 0.97, "n": 200}])
    ST.apply_index_relations(g, ctx)
    assert weak["confidence"] < 0.6 and weak["evidence"][-1] == "llm: same ids"  # an unverified claim never lifts it
    assert _edge(g, *key)["confidence"] == 0.9 and _edge(g, *key)["llm_boost"][1] == 0.9


def test_a_probed_edge_is_never_raised_even_by_a_verified_claim(env):
    _, g, ctx = env
    e = _edge(g, "shop.orders", ["address_id"], "shop.customer_addresses", ["id"])
    e["status"], e["confidence"] = "probed", 0.2  # a probe lowered it
    _store(env, {"relations": [_rel(from_table="shop.orders", from_columns=["address_id"], to_table="shop.customer_addresses",
                                    to_columns=["id"], confidence=0.9)]},
           [{"kind": "relations", "index": 0, "verified": True, "check": "relation_overlap", "rate": 0.97, "n": 200}])
    ST.apply_index_relations(g, ctx)
    assert e["confidence"] == 0.2 and "llm_boost" not in e and "llm: same ids" in e["evidence"]


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


def test_a_field_the_llm_filled_into_a_human_row_is_labelled_llm_in_describe_and_search(env):
    out, g, ctx = env
    _store(env, {"tables": [{"table": "shop.orders", "purpose": "Customer orders placed in the webshop", "reason": "r", "confidence": 0.7}],
                 "columns": [_col("shop.orders", "status", meaning="ignored", unit="state")]})
    d = describe_result(out, g, "shop.orders", min_confidence=0)
    assert d["provenance"]["notes"] == "verified" and d["provenance"]["columns"]["status"] == "verified"  # the human fields keep their label
    assert d["provenance"]["filled"]["notes"] == {"summary": {"label": "llm", "verified": False}}
    assert d["provenance"]["filled"]["columns"] == {"status": {"unit": {"label": "llm", "verified": False}}}
    from keygraph.search import format_describe, search
    text = format_describe(d)
    assert "summary: Customer orders placed in the webshop (llm)" in text and "[state]" in text and "(llm: unit)" in text
    r = next(t for t in search(g, ctx, "orders")["tables"] if t["table"] == "shop.orders")
    assert r["summary"] == "Customer orders placed in the webshop" and r["llm_fields"] == ["summary"] and r["verified"] is False
    other = next(t for t in search(g, ctx, "customers")["tables"] if t["table"] == "shop.customers")
    assert other["llm_fields"] == []


def test_llm_concept_links_on_a_human_concept_are_skipped_and_counted_as_kept(env):
    _, _, ctx = env
    before = [dict(r) for r in ctx.db.execute("SELECT * FROM concept_links WHERE concept='order' ORDER BY table_id, col")]
    res = _store(env, {"concepts": [{"name": "order", "definition": "other words", "tables": ["shop.carts", "shop.orders"],
                                     "reason": "r", "confidence": 0.7}]})
    assert [dict(r) for r in ctx.db.execute("SELECT * FROM concept_links WHERE concept='order' ORDER BY table_id, col")] == before
    assert ctx.db.execute("SELECT COUNT(*) FROM concept_links WHERE source='llm'").fetchone()[0] == 0
    assert res["kept"] >= 1


# --- fix round 1 ---------------------------------------------------------------------------------------------------

OVERRIDES = {"reject": [{"from": "billing.invoices.customer_id", "to": "shop.customers.id"},
                        {"from": "shop.orders.created_by_id"}],
             "confirm": [{"from": "shop.reviews.customer_id", "to": "shop.customers.id", "note": "checked"}],
             "add": [{"from": "shop.carts.id", "to": "shop.orders_shadow.id"}]}


def build_with_overrides(out):
    pytest.importorskip("yaml")
    out.mkdir(parents=True, exist_ok=True)
    (out.parent / f"{out.name}-ov.json").write_text(json.dumps(OVERRIDES))
    d = SHOP / "dumps"
    assert main(["load", "--dir", str(out), "--columns", str(d / "columns.json"), "--statistics", str(d / "statistics.json"),
                 "--key-columns", str(d / "key_columns.json"), "--tables", str(d / "tables.json"),
                 "--context", str(SHOP / "context.yaml"), "--overrides", str(out.parent / f"{out.name}-ov.json")]) == 0


@pytest.fixture()
def oenv(tmp_path):
    out = tmp_path / "ov"
    build_with_overrides(out)
    g = Graph.load(out / "graph.json")
    ctx = ContextDB(out / "keygraph.db", g)
    yield out, g, ctx
    ctx.close()


def test_graph_json_has_no_override_rejects_key_and_rejects_still_hold_after_a_reload(oenv):
    out, _, _ = oenv
    assert "override_rejects" not in json.loads((out / "graph.json").read_text())
    g2 = Graph.load(out / "graph.json")  # a fresh graph: the rejects come from the overrides saved in keygraph.db
    with ContextDB(out / "keygraph.db", g2) as ctx2:
        ST.store_unit(ctx2, g2, "job-0001", UNIT, {"relations": [
            _rel(from_table="billing.invoices", from_columns=["customer_id"], to_table="shop.customers", to_columns=["id"])]},
            "claude-x", "hash1", [])
        assert ST.apply_index_relations(g2, ctx2)["human_rejected"] == 1


def test_a_rejected_pair_is_never_re_added(oenv):
    _, g, ctx = oenv
    assert _edge(g, "billing.invoices", ["customer_id"], "shop.customers", ["id"]) is None
    _store(oenv, {"relations": [
        _rel(from_table="billing.invoices", from_columns=["customer_id"], to_table="shop.customers", to_columns=["id"]),
        _rel(from_table="shop.customers", from_columns=["id"], to_table="billing.invoices", to_columns=["customer_id"]),
        _rel(from_table="shop.orders", from_columns=["created_by_id"], to_table="shop.users", to_columns=["id"]),
        _rel(from_table="shop.orders", from_columns=["created_by_id"], to_table="shop.sellers", to_columns=["id"])]})
    res = ST.apply_index_relations(g, ctx)
    assert res["added"] == 0 and res["human_rejected"] == 4
    assert _edge(g, "billing.invoices", ["customer_id"], "shop.customers", ["id"]) is None
    assert not [e for e in g.edges if e.get("source") == "llm"]


def test_confirmed_and_added_edges_are_untouched(oenv):
    _, g, ctx = oenv
    conf = _edge(g, "shop.reviews", ["customer_id"], "shop.customers", ["id"])
    added = _edge(g, "shop.carts", ["id"], "shop.orders_shadow", ["id"])
    assert (conf["status"], added["status"]) == ("confirmed", "added")
    snap = json.dumps([conf, added], sort_keys=True)
    _store(oenv, {"relations": [
        _rel(from_table="shop.reviews", from_columns=["customer_id"], to_table="shop.customers", to_columns=["id"], confidence=0.99),
        _rel(from_table="shop.carts", from_columns=["id"], to_table="shop.orders_shadow", to_columns=["id"], confidence=0.99)]})
    res = ST.apply_index_relations(g, ctx)
    assert res["unchanged"] == 2 and res["merged"] == 0 and res["added"] == 0
    assert json.dumps([conf, added], sort_keys=True) == snap


def _prov_conf(ctx, ref):
    return _row(ctx, "fact_provenance", kind="relation", ref=ref)["confidence"]


def test_provenance_confidence_is_capped_and_verified_is_exactly_point_nine(env):
    _, g, ctx = env
    r1 = _rel()
    r2 = _rel(from_table="shop.carts", from_columns=["id"], confidence=0.3)
    r3 = _rel(from_table="shop.cart_items", from_columns=["cart_id"], confidence=0.99)
    ok = {"verified": True, "check": "relation_overlap", "rate": 0.9, "n": 10}
    _store(env, {"relations": [r1, r2, r3]}, [{"kind": "relations", "index": 1, **ok}, {"kind": "relations", "index": 2, **ok}])
    assert _prov_conf(ctx, "shop.returns(order_id)->shop.orders_shadow(id)") == 0.75
    assert _prov_conf(ctx, "shop.carts(id)->shop.orders_shadow(id)") == 0.9
    assert _prov_conf(ctx, "shop.cart_items(cart_id)->shop.orders_shadow(id)") == 0.9
    ST.apply_index_relations(g, ctx)
    assert _edge(g, "shop.carts", ["id"], "shop.orders_shadow", ["id"])["confidence"] == 0.9  # not tied to the answer's 0.3
    assert _edge(g, "shop.cart_items", ["cart_id"], "shop.orders_shadow", ["id"])["confidence"] == 0.9


def test_clear_llm_deletes_provenance_and_relations(env):
    _, _, ctx = env
    _store(env, {"relations": [_rel()], "columns": [_col()]})
    assert ctx.db.execute("SELECT COUNT(*) FROM fact_provenance").fetchone()[0] == 2
    ST.clear_llm(ctx)
    for t in ("fact_provenance", "index_relations", "column_semantics WHERE source='llm'"):
        assert ctx.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t


def test_relations_on_mirror_tables_are_skipped(env):
    _, g, ctx = env
    assert g.nodes["reporting.orders"].get("mirror_of")
    _store(env, {"relations": [_rel(from_table="reporting.orders", from_columns=["customer_id"], to_table="shop.customers", to_columns=["id"]),
                               _rel(from_table="shop.returns", from_columns=["order_id"], to_table="reporting.orders", to_columns=["id"])]})
    res = ST.apply_index_relations(g, ctx)
    assert res["stale"] == 2 and res["added"] == 0


def test_strip_removes_mirror_copies_of_llm_edges_only(env):
    _, g, _ = env
    llm = {"kind": "inferred", "status": "inferred", "source": "llm", "from": "a.x", "from_columns": ["p"], "to": "a.y", "to_columns": ["id"],
           "confidence": 0.7, "cardinality": "many-to-one", "ambiguous": False, "evidence": ["llm: r"], "id": "a.x(p)->a.y(id)"}
    copy = {**llm, "source": "mirror", "from": "m.x", "to": "m.y", "id": "m.x(p)->m.y(id)",
            "evidence": ["copy of a.x(p) -> a.y(id) in the original schema"]}
    other = {**copy, "from": "m.z", "id": "m.z(p)->m.y(id)", "evidence": ["copy of a.z(p) -> a.y(id) in the original schema"]}
    g.data["edges"] += [llm, copy, other]
    ST.strip_llm(g)
    ids = {e["id"] for e in g.edges}
    assert "a.x(p)->a.y(id)" not in ids and "m.x(p)->m.y(id)" not in ids and "m.z(p)->m.y(id)" in ids


def test_clear_llm_restores_null_and_whole_rows(env):
    _, _, ctx = env
    ctx.db.execute("UPDATE column_semantics SET null_meaning=NULL, unit=NULL WHERE table_id='shop.orders' AND col='total'")
    ctx.db.execute("UPDATE table_notes SET freshness=NULL WHERE table_id='shop.orders'")
    ctx.db.commit()
    before = [dict(r) for r in ctx.db.execute("SELECT * FROM column_semantics ORDER BY table_id, col")]
    nbefore = [dict(r) for r in ctx.db.execute("SELECT * FROM table_notes ORDER BY table_id")]
    _store(env, {"columns": [_col("shop.orders", "total", meaning="x", null_meaning="never null", unit="eur")],
                 "tables": [{"table": "shop.orders", "purpose": "p", "freshness": "hourly", "reason": "r", "confidence": 0.5}]})
    assert _row(ctx, "column_semantics", table_id="shop.orders", col="total")["unit"] == "eur"
    ST.clear_llm(ctx)
    assert [dict(r) for r in ctx.db.execute("SELECT * FROM column_semantics ORDER BY table_id, col")] == before
    assert [dict(r) for r in ctx.db.execute("SELECT * FROM table_notes ORDER BY table_id")] == nbefore


def test_an_old_index_relations_table_gains_cardinality_on_write_open(tmp_path):
    import sqlite3
    db = tmp_path / "k.db"
    ContextDB(db).close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE index_relations")
    con.execute("CREATE TABLE index_relations(rel_id TEXT PRIMARY KEY, unit_id TEXT, from_table TEXT, from_columns TEXT, to_table TEXT, "
                "to_columns TEXT, confidence REAL, reason TEXT, status TEXT, probe TEXT, model TEXT, job_id TEXT, at TEXT)")
    con.commit()
    con.close()
    raw = db.read_bytes()
    ro = ContextDB(db, readonly=True)
    assert ro.db.execute("SELECT COUNT(*) FROM index_relations").fetchone()[0] == 0
    ro.close()
    assert db.read_bytes() == raw  # a read-only open never alters the file
    ContextDB(db).close()
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(index_relations)")]
    assert cols[-1] == "cardinality"


def test_verdict_keys_are_whitelisted_and_answers_hold_only_accepted_fields(env):
    out, _, ctx = env
    v = {"kind": "relations", "index": 0, "verified": True, "check": "relation_overlap", "rate": 0.9, "n": 5,
         "leak": {"value": "hunter2"}, "error": "ValueError"}
    _store(env, {"relations": [_rel()]}, [v])
    dump = json.dumps([dict(r) for r in ctx.db.execute("SELECT probe FROM fact_provenance")]
                      + [dict(r) for r in ctx.db.execute("SELECT probe FROM index_relations")])
    assert "hunter2" not in dump and "leak" not in dump and "relation_overlap" in dump
    p = ST.save_answer(out, UNIT, "job-0001", {"relations": [{**_rel(), "extra": "x"}], "bogus": [{"a": 1}]}, "m", "h", [v])
    body = json.loads(p.read_text())
    assert "extra" not in body["answer"]["relations"][0] and "bogus" not in body["answer"]
    assert set(body["verdicts"][0]) <= {"kind", "index", "verified", "check", "rate", "n", "error", "skipped"}
