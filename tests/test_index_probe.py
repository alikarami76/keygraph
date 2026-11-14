import json
import shlex
import shutil
import sqlite3
import sys

import pytest
from test_example_shop import SHOP

from keygraph.cli import main
from keygraph.graph import Graph
from keygraph.index import probe as P
from keygraph.probe import SelectOnlyRunner, UnsafeSQL


@pytest.fixture(scope="module")
def shop_graph(tmp_path_factory):
    """The demo map from its dumps (no context file, so no extras are needed)."""
    out, d = tmp_path_factory.mktemp("probeshop"), SHOP / "dumps"
    assert main(["load", "--dir", str(out), "--columns", str(d / "columns.json"), "--statistics", str(d / "statistics.json"),
                 "--key-columns", str(d / "key_columns.json"), "--tables", str(d / "tables.json")]) == 0
    return Graph.load(out / "graph.json")


@pytest.fixture()
def shop_db(tmp_path):
    """A copy of shop.db where the product ids no longer overlap any other id column."""
    db = tmp_path / "shop.db"
    shutil.copy(SHOP / "shop.db", db)
    con = sqlite3.connect(db)
    con.execute("UPDATE products SET id = id + 1000000")
    con.commit()
    con.close()
    return db


@pytest.fixture()
def src(shop_db, monkeypatch):
    monkeypatch.setenv("KG_TEST_SHOP_URL", f"sqlite:///{shop_db}")
    return P.runner_from_env("KG_TEST_SHOP_URL", "")


def _ent(kind, item, index=0):
    return {"kind": kind, "index": index, "item": item}


def _rel(ft, fc, tt, tc):
    return _ent("relations", {"from_table": ft, "from_columns": fc, "to_table": tt, "to_columns": tc, "reason": "r", "confidence": 0.7})


def _run(src, g, ents, **kw):
    return P.probe_items(ents, g, src.runner, src.quote, bare=src.bare, **kw)


def test_true_relation_passes_and_planted_false_one_fails(src, shop_graph):
    ok, bad = _run(src, shop_graph, [_rel("billing.payments", ["order_id"], "shop.orders", ["id"]),
                                     _rel("shop.orders", ["id"], "shop.products", ["id"])])
    assert ok["verified"] is True and ok["rate"] == 1.0 and ok["n"] > 0 and ok["check"] == "relation_overlap"
    assert bad["verified"] is False and bad["rate"] == 0.0


def test_partial_overlap_is_a_rate_below_the_bar(src, shop_graph):
    (v,) = _run(src, shop_graph, [_rel("shop.orders", ["id"], "shop.customers", ["id"])])  # ids 1..120 against 1..60
    assert v["verified"] is False and v["rate"] == 0.5


def test_multi_column_relation_stays_unverified(src, shop_graph):
    (v,) = _run(src, shop_graph, [_rel("shop.orders", ["id", "customer_id"], "shop.orders", ["id", "customer_id"])])
    assert v["verified"] is None and v["check"] == "relation_overlap_multi_column"


def _codes(codes, col="status", table="shop.orders"):
    return _ent("columns", {"table": table, "column": col, "meaning": "m", "codes": codes, "reason": "r", "confidence": 0.7})


def test_present_codes_pass_and_invented_code_fails(src, shop_graph):
    ok, bad, partial, none = _run(src, shop_graph, [_codes({"1": "placed", "2": "paid", "9": "odd"}), _codes({"77": "invented"}),
                                                     _codes({"1": "placed", "77": "invented"}),
                                                     _ent("columns", {"table": "shop.orders", "column": "status", "meaning": "m",
                                                                      "reason": "r", "confidence": 0.5})])
    assert ok["verified"] is None and ok["seen"] is True and ok["rate"] == 1.0 and ok["n"] == 3  # values occur: not a verification
    assert "labels" not in ok["why"] and "not checked" in ok["why"]
    assert bad["verified"] is False and bad["rate"] == 0.0
    assert partial["verified"] is False and partial["rate"] == 0.5
    assert none["verified"] is None and none["check"] == "none"


def test_text_codes_are_matched_as_text(src, shop_graph):
    ok, bad = _run(src, shop_graph, [_codes({"EUR": "euro"}, "currency"), _codes({"XXX": "none"}, "currency")])
    assert ok["verified"] is None and ok["seen"] is True and bad["verified"] is False and "seen" not in bad


def _table(**kw):
    return _ent("tables", {"table": "shop.orders", "purpose": "p", "reason": "r", "confidence": 0.7, **kw})


def test_grain_probe_is_sample_based_and_skips_the_trivial_primary_key(src, shop_graph):
    pk, pk_named, missing, dup, ok, none = _run(src, shop_graph, [
        _table(grain="one row per customer"), _table(grain="g", grain_columns=["id"]), _table(grain="g", grain_columns=[]),
        _table(grain="one row per customer", grain_columns=["customer_id"]),
        _table(grain="g", grain_columns=["id", "customer_id"]), _table()])
    for v in (pk, pk_named, missing):
        assert v["verified"] is None and v["check"] == "grain_pk_trivial"
    assert dup["verified"] is False and dup["check"] == "grain_sample_unique" and dup["rate"] == 0.5 and dup["n"] == 120
    assert ok["verified"] is True and ok["check"] == "grain_sample_unique" and ok["n"] == 120
    assert none["verified"] is None and none["check"] == "none"


def test_identifiers_double_their_quote_character():
    assert P.quote_ident("a\"b", '"') == '"a""b"' and P.quote_ident("a`b", "`") == "`a``b`"
    assert P.quote_ident("a]b", "[]") == "[a]]b]"


@pytest.mark.parametrize("kind,quote", [("mysql", "`"), ("mariadb", "`"), ("clickhouse", "`"), ("sqlserver", "[]"), ("postgres", '"'),
                                        ("sqlite", '"'), ("", '"'), (None, '"')])
def test_quote_follows_the_dialect(kind, quote):
    assert P.quote_for(kind) == quote


def test_cmd_runner_takes_its_dialect_from_the_caller_and_the_map(shop_graph, monkeypatch):
    monkeypatch.setenv("KG_TEST_C", "true {sql}")
    assert P.runner_from_env("", "KG_TEST_C", dialect="postgres").quote == '"'
    assert P.runner_from_env("", "KG_TEST_C", dialect="mysql").quote == "`"
    assert P.runner_from_env("", "KG_TEST_C").quote == '"'
    shop_graph.build = {"sources": [{"kind": "mariadb"}]}
    assert P.dialect_of(shop_graph) == "mariadb"
    shop_graph.build = None
    assert P.dialect_of(shop_graph) is None


def test_default_filter_selectivity(src, shop_graph):
    ok, empty, free_sql, unknown = _run(src, shop_graph, [
        _table(default_filters=["status = 2", "customer_id IS NOT NULL"]), _table(default_filters=["status = 77"]),
        _table(default_filters=["status = 2; DROP TABLE orders", "1=1 OR status = 2"]), _table(default_filters=["deleted_at IS NULL"])])
    assert ok["verified"] is True and ok["n"] == 120 and ok["rate"] == round(37 / 120, 3)
    assert empty["verified"] is False and empty["rate"] == 0.0
    assert free_sql["verified"] is None and free_sql["check"].endswith("_unparsed")
    assert unknown["verified"] is None


def test_filter_text_comes_from_the_strict_grammar_only():
    def q(c):
        return f'"{c}"'

    cols = {"status", "deleted_at", "kind"}
    assert P.parse_filter("status IN (1, 2) and deleted_at is null", cols, q) == ('"status" IN (1,2) AND "deleted_at" IS NULL',
                                                                                  {"status", "deleted_at"})
    assert P.parse_filter("kind <> 'a''b'", cols, q)[0] == "\"kind\" <> 'a''b'"
    assert P.parse_filter("status NOT IN (1)", cols, q)[0] == '"status" NOT IN (1)'
    for bad in ["status = 1 OR kind = 2", "status = (SELECT 1)", "status = 1 AND", "name = 1", "status > 1", "status = 'a\\'",
                "status = 1; DROP", "status = status", "", "status IN ()", "status = 1 --"]:
        assert P.parse_filter(bad, cols, q) is None, bad


def test_no_env_var_means_unverified(shop_graph, monkeypatch):
    monkeypatch.delenv("KG_TEST_NOPE", raising=False)
    assert P.runner_from_env("KG_TEST_NOPE", "") is None and P.runner_from_env("", "") is None
    monkeypatch.setenv("KG_TEST_EMPTY", "")
    assert P.runner_from_env("KG_TEST_EMPTY", "") is None
    ents = [_rel("shop.orders", ["id"], "shop.customers", ["id"]), _codes({"1": "x"}), _ent("concepts", {"name": "n"})]
    out = P.probe_items(ents, shop_graph, None, '"')
    assert [v["verified"] for v in out] == [None, None, None] and out[0]["check"] == "no_source" and out[2]["check"] == "none"


def test_write_statements_raise_unsafe_sql_and_the_file_is_read_only(src, shop_db):
    for sql in ("DELETE FROM orders", "UPDATE orders SET total = 0", "DROP TABLE orders", "SELECT 1; DELETE FROM orders",
                "INSERT INTO orders(id) VALUES (999)"):
        with pytest.raises(UnsafeSQL):
            src.runner.run(sql)
    with pytest.raises(sqlite3.OperationalError):  # even past the guard, the connection is opened mode=ro
        src.runner._execute("DELETE FROM orders")
    assert sqlite3.connect(shop_db).execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 120


def test_sqlite_url_needs_a_file():
    for url in ("sqlite://", "sqlite:///:memory:", "sqlite:///"):
        with pytest.raises(ValueError):
            SelectOnlyRunner.from_url(url)


def test_sqlite_runner_returns_dicts_when_asked(shop_db):
    r = SelectOnlyRunner.from_url(f"sqlite:///{shop_db}", as_dicts=True).run("SELECT id AS a, status FROM orders ORDER BY id LIMIT 1")
    assert r == [{"a": 1, "status": r[0]["status"]}]


def test_cmd_runner_probes_through_the_command(shop_graph, shop_db, tmp_path, monkeypatch):
    script = tmp_path / "q.py"
    script.write_text("import sys, sqlite3, json\n"
                      f"con = sqlite3.connect('file:{shop_db}?mode=ro', uri=True)\ncur = con.execute(sys.argv[1])\n"
                      "names = [d[0] for d in cur.description]\n"
                      "print('log line'); print(json.dumps([dict(zip(names, r)) for r in cur.fetchall()]))\n")
    monkeypatch.setenv("KG_TEST_SHOP_CMD", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{sql}}")
    s = P.runner_from_env("", "KG_TEST_SHOP_CMD")
    assert s.quote == '"' and not s.bare
    with pytest.raises(UnsafeSQL):
        s.runner.run("DELETE FROM orders")
    ok = P.probe_items([_codes({"1": "placed"}, table="shop.orders")], shop_graph, s.runner, '"', bare=True)  # `bare` as sqlite ids
    assert ok[0]["seen"] is True


def test_probe_output_holds_rates_and_counts_never_values(src, shop_graph):
    seen: list[str] = []
    inner = src.runner

    def spy(sql):
        rows = inner.run(sql)
        seen.extend(str(v) for r in rows for v in r)
        return rows

    ents = [_codes({"EUR": "euro"}, "currency"), _rel("billing.payments", ["gateway_ref"], "shop.orders", ["currency"]),
            _table(default_filters=["currency = 'EUR'"], grain="g")]
    out = P.probe_items(ents, shop_graph, SelectOnlyRunner(spy), '"', bare=True)
    text = json.dumps(out)
    assert any(s == "EUR" for s in seen) and "EUR" not in text
    assert all(s not in text for s in seen if len(s) >= 3 and not s.isdigit())
    assert set(out[0]) == {"kind", "index", "verified", "check", "rate", "n", "seen", "why"}


def test_query_cap_leaves_the_rest_unverified(src, shop_graph):
    ents = [_rel("billing.payments", ["order_id"], "shop.orders", ["id"]), _codes({"1": "placed"}), _codes({"2": "paid"})]
    a, b, c = _run(src, shop_graph, ents, cap=3)
    assert a["verified"] is True and b["seen"] is True and c["verified"] is None and c["skipped"] == "query_cap" and c["why"]
    assert P.QUERY_CAP == 40


def test_errors_give_the_type_never_the_message(shop_graph):
    def boom(sql):
        raise RuntimeError("password=hunter2 value 4111111111111111")

    out = P.probe_items([_codes({"1": "x"}), _table(grain="g", grain_columns=["customer_id"])], shop_graph, SelectOnlyRunner(boom), '"')
    assert [v["verified"] for v in out] == [None, None] and out[0]["error"] == "RuntimeError" and out[1]["error"] == "RuntimeError"
    assert "hunter2" not in json.dumps(out) and "4111" not in json.dumps(out)


def test_non_sqlite_url_without_the_live_extra_says_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlalchemy", None)  # makes `import sqlalchemy` fail
    monkeypatch.setenv("KG_TEST_PG", "postgresql://u:p@h/db")
    with pytest.raises(RuntimeError, match="live extra"):
        P.runner_from_env("KG_TEST_PG", "")


def test_time_budget_marks_the_rest_unverified_and_stops_queries(src, shop_graph, monkeypatch):
    calls = []
    real = src.runner.run
    monkeypatch.setattr(src.runner, "run", lambda sql: calls.append(sql) or real(sql))
    ents = [_rel("billing.payments", ["order_id"], "shop.orders", ["id"]), _rel("shop.orders", ["id"], "shop.products", ["id"])]
    out = _run(src, shop_graph, ents, time_budget=0)
    assert calls == [] and [v["check"] for v in out] == ["time_budget"] * 2 and all(v["verified"] is None for v in out)
    assert _run(src, shop_graph, ents, time_budget=None)[0]["verified"] is True


def test_time_budget_runs_out_between_items(src, shop_graph, monkeypatch):
    clock = iter([0.0, 0.5, 0.5, 0.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    monkeypatch.setattr(P.time, "monotonic", lambda: next(clock, 5.0))
    ents = [_rel("billing.payments", ["order_id"], "shop.orders", ["id"]), _rel("shop.orders", ["id"], "shop.products", ["id"])]
    first, second = _run(src, shop_graph, ents, time_budget=1)
    assert first["verified"] is True and second["check"] == "time_budget" and second["verified"] is None


def test_a_verdict_that_checked_nothing_says_why(src, shop_graph):
    """The demo's orders.created_by_id is NULL in every row, so there is nothing to sample: n 0, verified null, and a reason."""
    ok, null_rel, trivial = _run(src, shop_graph, [
        _rel("billing.payments", ["order_id"], "shop.orders", ["id"]),
        _rel("shop.orders", ["created_by_id"], "shop.users", ["id"]),
        _ent("tables", {"table": "shop.orders", "grain": "one row per order", "grain_columns": ["id"], "reason": "r", "confidence": 0.7})])
    assert "why" not in ok
    assert null_rel["verified"] is None and null_rel["n"] == 0 and null_rel["why"] == "no non-null values sampled"
    assert trivial["verified"] is None and trivial["n"] == 0 and "primary or unique key" in trivial["why"]
    (nosrc,) = P.probe_items([_rel("shop.orders", ["id"], "shop.customers", ["id"])], shop_graph, None, '"')
    assert nosrc["why"] == "no probe source"


def test_every_null_verdict_carries_a_why(src, shop_graph):
    ents = [_codes({"1": "x"}), _codes({}), _table(), _table(default_filters=["status = 77 AND"]),
            _table(grain="g", grain_columns=["id"]), _ent("concepts", {"name": "c", "definition": "d", "tables": [], "reason": "r", "confidence": 0.5}),
            _rel("billing.payments", ["order_id", "id"], "shop.orders", ["id", "id"])]
    out = _run(src, shop_graph, ents, cap=1)
    out += _run(src, shop_graph, ents, cap=40)
    out += P.probe_items(ents, shop_graph, None)
    out += P.probe_items(ents, shop_graph, src.runner, '"', bare=True, time_budget=0.0)

    def boom(sql):
        raise RuntimeError("x")

    out += P.probe_items([_codes({"1": "x"}), _table(grain="g", grain_columns=["customer_id"])], shop_graph, SelectOnlyRunner(boom), '"')
    nulls = [v for v in out if v["verified"] is None]
    assert nulls and all(v.get("why") for v in nulls), [v for v in nulls if not v.get("why")]
