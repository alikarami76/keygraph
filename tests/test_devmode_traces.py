"""Dev mode, internal decision traces: build traces, inference/mirror/hub/resolve/search/path decisions, the row-tool
response policy, and invariant 7 (dev on or off, the same bytes)."""

import json
import os
import stat
import shutil

import pytest

from keygraph import devlog
from keygraph.cli import main, run_pipeline
from keygraph.graph import Graph
from keygraph.model import Column, Index, Schema, Table
from tests.test_example_shop import _build

PK = [Index("PRIMARY", ["id"], True, True)]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("KEYGRAPH_DEV", raising=False)
    monkeypatch.delenv("KEYGRAPH_DEV_SESSION", raising=False)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")


def dev_marker(out):
    (out / "dev").mkdir(parents=True, exist_ok=True)
    (out / "dev" / "enabled").write_text("1")


def traces(out, name=""):
    return sorted((out / "dev" / "traces").glob(f"*{name}*.jsonl" if name else "*.jsonl"))


def read(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def events(out):
    p = out / "dev" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture(scope="module")
def demo_on(tmp_path_factory):
    d = tmp_path_factory.mktemp("trace_on")
    dev_marker(d)
    _build(d, profile=True)
    return d


@pytest.fixture
def out(demo_on, tmp_path):
    d = tmp_path / "o"
    shutil.copytree(demo_on, d)
    return d


# ---- build traces -----------------------------------------------------------------------------------------

def test_build_trace_has_phases_and_event_links_to_it(demo_on):
    (load,) = traces(demo_on, "load")
    evs = read(load)
    phases = {e["name"]: e for e in evs if e["kind"] == "phase"}
    for name in ("build_graph", "index_graph", "context_import", "reindex", "apply_context", "render.mirrors_detect",
                 "render.mirrors_transfer", "render.code_joins", "render.hubs", "render.areas", "render.docs",
                 "render.graph_json", "render.report", "render.html"):
        assert name in phases and phases[name]["ms"] >= 0, name
    assert phases["build_graph"]["tables"] == 35 and phases["render.docs"]["pages"] > 30
    load_ev = next(e for e in events(demo_on) if e["tool"] == "load")
    assert load_ev["trace"] == load.stem and load_ev["kind"] == "command"
    assert "decisions" not in load_ev  # a build's decisions go to the trace, not the event


def test_every_long_task_has_a_trace_linked_from_its_event(out, tmp_path):
    shop = out.parent  # noqa: F841
    from tests.test_example_shop import SHOP
    assert main(["code", "index", str(SHOP / "app"), "--name", "shop-app", "--dir", str(out)]) == 0
    assert main(["queries", "import", str(SHOP / "queries.jsonl"), "--dir", str(out)]) == 0
    assert main(["render", "--queries", "--dir", str(out)]) == 0
    by_tool = {e["tool"]: e for e in events(out) if e["kind"] == "command" and "trace" in e}
    assert {"code index", "queries import", "render"} <= set(by_tool)
    idx = {p.stem: read(p) for p in traces(out)}
    code = idx[by_tool["code index"]["trace"]]
    assert {"code.walk", "code.declarations_and_sql", "code.uses", "code.store"} <= {e.get("name") for e in code}
    q = idx[by_tool["queries import"]["trace"]]
    imp = next(e for e in q if e.get("name") == "queries.import")
    assert imp["added"] > 0 and "sql" not in json.dumps(imp).lower().replace("queries.import", "")
    assert any(e.get("name") == "render.docs" for e in idx[by_tool["render"]["trace"]])


def test_profile_and_probe_traces_hold_timings_and_counts_only(out, tmp_path):
    from tests.test_example_shop import SHOP
    url = f"sqlite:///{SHOP / 'shop.db'}"
    pytest.importorskip("sqlalchemy")
    assert main(["profile", "--dir", str(out), "--source", "sqlite", "--url", url, "--bare-tables", "--tables", "shop.orders"]) == 0
    assert main(["probe", "--dir", str(out), "--url", url, "--sample", "20"]) == 0
    cmds = {e["tool"]: e for e in events(out) if e["kind"] == "command" and "trace" in e}
    prof = next(read(p) for p in traces(out) if p.stem == cmds["profile"]["trace"])
    pt = next(e for e in prof if e["kind"] == "profile.table")
    assert pt["table"] == "shop.orders" and pt["columns"] > 0 and pt["outcome"] == "profiled"
    probe = next(read(p) for p in traces(out) if p.stem == cmds["probe"]["trace"])
    assert any(e["kind"] == "probe.summary" for e in probe)
    raw = "".join(p.read_text() for p in traces(out))
    assert "top_values" not in raw and "SELECT" not in raw and "alice" not in raw.lower()


def test_dev_off_writes_nothing_and_creates_no_tracer(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a tracer was created with dev off")
    monkeypatch.setattr(devlog, "Tracer", boom)
    monkeypatch.setattr(devlog, "Recorder", boom)
    _build(tmp_path)
    assert main(["path", "shop.customers", "shop.shipments", "--dir", str(tmp_path)]) == 0
    assert not (tmp_path / "dev").exists() and not devlog.active()


def test_tiny_trace_cap_writes_truncated_and_the_build_still_succeeds(tmp_path, monkeypatch):
    ref, capped = tmp_path / "ref", tmp_path / "cap"
    _build(ref)
    monkeypatch.setattr(devlog, "TRACE_MAX_EVENTS", 7)
    dev_marker(capped)
    _build(capped)
    for t in traces(capped):
        kinds = [e["kind"] for e in read(t)]
        assert kinds.count("truncated") == 1 and kinds[-1] == "truncated" and len(kinds) == 8
    assert (ref / "graph.json").read_bytes() == (capped / "graph.json").read_bytes()


# ---- decisions in build traces ----------------------------------------------------------------------------

def test_rejected_inference_candidate_has_its_reason(demo_on):
    evs = read(traces(demo_on, "load")[0])
    col = next(e for e in evs if e["kind"] == "infer.column" and e["table"] == "shop.product_images" and e["column"] == "product_id")
    kept = next(c for c in col["candidates"] if c["outcome"] == "kept")
    rej = next(c for c in col["candidates"] if c["outcome"] == "rejected")
    assert kept["to"] == "shop.products" and rej["to"] == "reporting.products"
    assert "same-schema" in rej["reason"] and rej["base"] > 0 and rej["reasons"] and "scored" in rej
    assert kept["confidence"] >= 0.3 and kept["reasons"]
    assert any(e["kind"] == "infer.declared" and e["edges"] == 18 for e in evs)


def tiny(extra_cols=(), tables=()):
    s = Schema()
    s.add(Table("app", "customers", [Column("id", "int"), Column("name", "varchar(50)")], list(PK)))
    s.add(Table("app", "orders", [Column("id", "int"), Column("customer_id", "int"), Column("ghost_id", "int")] + list(extra_cols), list(PK)))
    return s


def test_unresolved_no_target_key_and_below_min_confidence_reasons(tmp_path):
    s = tiny()
    s.add(Table("app", "ghosts", [Column("code", "int")], []))  # no keys and no id column: cannot be a target
    s.add(Table("app", "sales_regions", [Column("id", "varchar(10)")], list(PK)))  # weak tail match plus a type mismatch
    s.tables["app.orders"].columns.append(Column("region_id", "int"))
    dev_marker(tmp_path)
    run_pipeline(s, tmp_path, verbose=False)
    evs = read(traces(tmp_path)[0])
    cols = {(e["table"], e["column"]): e for e in evs if e["kind"] == "infer.column"}
    ghost = cols[("app.orders", "ghost_id")]
    assert ghost["outcome"] == "unresolved"
    assert [(c["to"], c["outcome"], c["reason"]) for c in ghost["candidates"]] == [("app.ghosts", "rejected", "no target key")]
    region = cols[("app.orders", "region_id")]
    (c,) = region["candidates"]
    assert region["outcome"] == "unresolved" and c["outcome"] == "rejected" and c["reason"] == "below min_confidence (0.3)"
    assert c["base"] == 0.35 and c["confidence"] < 0.3 and any("weak" in r for r in c["reasons"])
    assert cols[("app.orders", "customer_id")]["outcome"] == "kept"
    assert next(e for e in evs if e["kind"] == "infer.summary")["unresolved"] == 2


def test_top_k_cut_and_ambiguity_penalty(tmp_path):
    s = Schema()
    for sch in ("a", "b", "c", "d"):  # four same-name candidates in four schemas, none in the column's own
        s.add(Table(sch, "accounts", [Column("id", "int")], list(PK)))
    s.add(Table("x", "ledger", [Column("id", "int"), Column("account_id", "int")], list(PK)))
    dev_marker(tmp_path)
    run_pipeline(s, tmp_path, verbose=False)
    evs = read(traces(tmp_path)[0])
    col = next(e for e in evs if e["kind"] == "infer.column" and e["column"] == "account_id")
    assert col["ambiguous"] is True
    assert [c.get("ambiguity_penalty") for c in col["candidates"]] == [-0.1, -0.1, -0.1, None]
    cut = col["candidates"][3]
    assert cut["outcome"] == "rejected" and cut["reason"] == "cut by top_k (3)"


def test_mirror_and_hub_decisions_are_traced(demo_on):
    evs = read(traces(demo_on, "load")[0])
    pairs = [e for e in evs if e["kind"] == "mirror.pair"]
    assert any(p["schema"] == "reporting" and p["best_match"] == "shop" and p["share"] >= p["threshold"] for p in pairs)
    dec = next(e for e in evs if e["kind"] == "mirror.decision")
    assert dec["copy"] == "reporting" and dec["original"] == "shop" and dec["verdict"] == "mirror" and dec["reason"]
    assert next(e for e in evs if e["kind"] == "mirror.result")["mirrors"] == {"reporting": "shop"}
    assert any(e["kind"] == "mirror.transfer" and "edges" in e for e in evs)
    hub = next(e for e in evs if e["kind"] == "hub.decision" and e["table"] == "shop.users")
    assert hub["verdict"] == "picked" and hub["incoming"] >= 11 and "threshold" in hub["reason"]
    thr = next(e for e in evs if e["kind"] == "hub.threshold")
    assert thr["threshold"] >= 5 and thr["picked"] == 1
    assert any(e["kind"] == "hub.decision" and e["verdict"] == "not a hub" for e in evs)


def test_override_effects_are_traced(tmp_path):
    s = tiny()
    ov = tmp_path / "ov.json"
    ov.write_text(json.dumps({"confirm": [{"from": "app.orders.customer_id", "to": "app.customers.id", "note": "secret-note"}],
                              "reject": [{"from": "app.orders.ghost_id"}],
                              "add": [{"from": "app.orders.ghost_id", "to": "app.customers.id"}],
                              "aliases": {"ghost": "app.customers"}}))
    dev_marker(tmp_path)
    run_pipeline(s, tmp_path, overrides=str(ov), verbose=False)
    evs = read(traces(tmp_path)[0])
    by = {e["kind"]: e for e in evs if e["kind"].startswith("override.")}
    assert by["override.config"]["aliases"] == 1
    assert by["override.confirmed"]["effect"].startswith("raised") and by["override.added"]["effect"] == "added a new edge"
    assert "removed" in by["override.reject"] and "secret-note" not in json.dumps(evs)


# ---- decisions on call events -----------------------------------------------------------------------------

def ambiguous_dir(tmp_path):
    s = Schema()
    s.add(Table("a", "items", [Column("id", "int"), Column("sku", "varchar(10)")], list(PK)))
    s.add(Table("b", "items", [Column("id", "int"), Column("weight", "decimal(10,2)"), Column("note", "text")], list(PK)))
    run_pipeline(s, tmp_path, verbose=False)
    return tmp_path


def resolves(ev):
    return [d for d in ev.get("decisions", []) if d["kind"] == "resolve"]


def test_ambiguous_resolve_over_cli_records_rule_and_candidates(tmp_path):
    d = ambiguous_dir(tmp_path)
    dev_marker(d)
    assert main(["keys", "items", "--dir", str(d)]) == 2
    (ev,) = events(d)
    (r,) = resolves(ev)
    assert r["rule"] == "ambiguous" and r["candidates"] == ["a.items", "b.items"] and r["name"] == "items" and not ev["ok"]
    assert main(["keys", "A.ITEMS", "--dir", str(d)]) == 0
    assert resolves(events(d)[1])[0]["rule"] == "case_insensitive"
    assert main(["keys", "itemz", "--dir", str(d)]) == 2
    unk = resolves(events(d)[2])[0]
    assert unk["rule"] == "unknown" and "a.items" in unk["suggestions"]


def test_resolve_rules_exact_bare_and_original_over_mirror(out):
    assert main(["keys", "shop.orders", "--dir", str(out)]) == 0
    assert main(["keys", "customers", "--dir", str(out)]) == 0   # shop and reporting both have it
    assert main(["keys", "payments", "--dir", str(out)]) == 0
    ev = [resolves(e)[0] for e in events(out) if e["kind"] == "command" and e["tool"] == "keys"]
    assert [r["rule"] for r in ev] == ["exact", "original_over_mirror_or_copy", "unique_bare_name"]
    assert ev[1]["table"] == "shop.customers" and "reporting.customers" in ev[1]["candidates"]


def test_mcp_ambiguous_call_carries_the_rule(tmp_path):
    pytest.importorskip("mcp")
    from keygraph.mcp_server import build_server
    from tests.test_mcp import call
    d = ambiguous_dir(tmp_path)
    dev_marker(d)
    srv = build_server(d)
    err, _ = call(srv, "keys", table="items")
    assert err
    (ev,) = events(d)
    assert resolves(ev)[0]["rule"] == "ambiguous" and resolves(ev)[0]["candidates"] == ["a.items", "b.items"]


def test_path_call_carries_candidate_scores_and_the_winning_reason(out):
    assert main(["path", "shop.customers", "shop.shipments", "--dir", str(out), "--json"]) == 0
    ev = events(out)[-1]
    rank = next(d for d in ev["decisions"] if d["kind"] == "path.rank")
    assert rank["pops"] > 0 and rank["candidates"] and rank["winner"] == 0 and rank["winner_reason"]
    c = rank["candidates"][0]
    assert c["raw_score"] > 0 and c["steps"] and {"hop", "to", "penalties", "factor", "cardinality"} <= set(c["steps"][0])
    assert any("fan_out" in s["penalties"] for s in c["steps"])
    assert set(rank["skipped"]) == {"below_min_confidence", "already_on_path", "mirror_table", "parallel_edge_cap"}
    assert {r["name"] for r in resolves(ev)} == {"shop.customers", "shop.shipments"}


def test_why_the_winner_won_for_a_tie_on_score(tmp_path):
    from keygraph import query as Q
    g = Graph.load(ambiguous_dir(tmp_path) / "graph.json")
    with devlog.collecting() as dec:
        assert Q.find_paths(g, "a.items", "b.items") == []
    assert dec[-1]["kind"] == "path.rank" and dec[-1]["winner_reason"] == "no path found" and dec[-1]["candidates"] == []


def test_search_call_carries_contributions_and_the_cutoff(out, capsys):
    assert main(["search", "orders", "--limit", "2", "--json", "--dir", str(out)]) == 0
    capsys.readouterr()
    ev = events(out)[-1]
    sc = next(d for d in ev["decisions"] if d["kind"] == "search.score")
    assert len(sc["tables"]) == 2 and sc["cutoff"]["limit"] == 2 and sc["cutoff"]["first_cut_score"] is not None
    top = sc["tables"][0]
    assert top["contributions"] and {"kind", "ref", "weight"} == set(top["contributions"][0])
    assert abs(sum(c["weight"] for c in top["contributions"]) - top["score"]) < 0.01 or len(top["contributions"]) == 12


# ---- invariant 7 ------------------------------------------------------------------------------------------

def _tree(out):
    files = [out / "graph.json", out / "RELATIONS_REPORT.md", out / "keygraph.html", out / "build.json"]
    files += sorted((out / "areas").glob("*.md")) + sorted((out / "tables").glob("*.md"))
    return {str(p.relative_to(out)): p.read_bytes() for p in files}


def _dump(out):
    """keygraph.db as SQL text; wall-clock stamps (code index and query import write them) are blanked."""
    import re
    import sqlite3
    con = sqlite3.connect(out / "keygraph.db")
    try:
        return re.sub(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(\.\d+)?", "<ts>", "\n".join(con.iterdump()))
    finally:
        con.close()


def test_dev_on_and_off_builds_are_byte_identical(tmp_path, monkeypatch):
    import datetime
    import types

    from keygraph import profile

    class Frozen(datetime.datetime):  # profiled_at is wall-clock (to the second) in main too; freeze it so equal builds match
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 12, 10, 0, 0)
    monkeypatch.setattr(profile, "dt", types.SimpleNamespace(**{**vars(datetime), "datetime": Frozen}))
    off, on = tmp_path / "off", tmp_path / "on"
    _build(off, profile=True, app=True)
    dev_marker(on)
    _build(on, profile=True, app=True)
    assert traces(on) and not (off / "dev").exists()
    assert _dump(off) == _dump(on) and "profiled_at" in _dump(on)
    a, b = _tree(off), _tree(on)
    assert a.keys() == b.keys() and len(a) > 40
    assert [k for k in a if a[k] != b[k]] == []
    # build.json is the one file allowed to differ between identical builds; with SOURCE_DATE_EPOCH fixed it does not
    again = tmp_path / "again"
    _build(again, profile=True, app=True)
    assert _tree(again)["build.json"] == a["build.json"]


def test_tool_outputs_do_not_change_when_decisions_are_collected(out):
    from keygraph import cli
    from keygraph import search as S
    from keygraph.context import ContextDB
    g = Graph.load(out / "graph.json")

    def outputs():
        with ContextDB(out / "keygraph.db", g, readonly=True) as ctx:
            return json.dumps([cli.path_result(g, "shop.customers", "shop.shipments"), S.search(g, ctx, "orders"),
                               cli.describe_result(out, g, "shop.orders", True)], sort_keys=True, default=str)
    plain = outputs()
    with devlog.collecting() as dec:
        assert outputs() == plain
    assert dec  # and the collector did see decisions


# ---- row-returning tools never log rows -------------------------------------------------------------------

ROWS = {"columns": [{"name": "email", "type": "varchar"}, {"name": "n", "type": "int"}],
        "rows": [["alice@example.com", 1], ["bob@example.com", 2]], "row_count": 2, "truncated": False,
        "warnings": ["no LIMIT; one was added"], "checks": {"read_only": True, "limit_applied": 100}, "sql": "SELECT 1"}


def test_row_policy_registry_and_shape_helpers():
    assert devlog.is_row_tool("run_query") and devlog.is_row_tool("run") and not devlog.is_row_tool("search")
    got = devlog.response_fields("run_query", ROWS)["response"]
    assert got == {"row_count": 2, "columns": [{"name": "email", "type": "varchar"}, {"name": "n", "type": "int"}],
                   "truncated": False, "warnings": ["no LIMIT; one was added"], "checks": {"read_only": True, "limit_applied": 100}}
    big = {**ROWS, "rows": [["x" * 100, i] for i in range(5000)], "row_count": 5000, "truncated": True, "data": ["leak"]}
    got = devlog.response_fields("run_query", big)
    assert list(got) == ["response"] and got["response"]["row_count"] == 5000 and got["response"]["truncated"] is True
    assert "xxxx" not in json.dumps(got) and "leak" not in json.dumps(got)
    assert devlog.response_fields("run_query", [[1], [2]])["response"] == {"row_count": 2}
    # dict rows: columns come from the first row; a bad shape never raises
    assert devlog.rows_shape({"rows": [{"a": 1}]})["columns"] == [{"name": "a", "type": None}]
    assert devlog.rows_shape("oops") == {"row_count": None}
    # CLI stdout of a row command: shape from JSON, a hash and line count for text, never the text itself
    text = json.dumps(ROWS)
    r = devlog.stdout_fields("run", text, len(text), "h", 9)
    assert r["response"]["row_count"] == 2 and "alice" not in json.dumps(r)
    r = devlog.stdout_fields("run", "alice@example.com | 1\nbob@example.com | 2\n", 40, "h", 9, True, 2)
    assert r == {"response_ref": {"sha256": "h", "bytes": 40, "tokens_approx": 9, "shape": {"lines": 2}}}
    devlog.register_row_tool("fake_rows")
    try:
        assert "alice" not in json.dumps(devlog.response_fields("fake_rows", ROWS))
    finally:
        devlog.ROW_TOOLS.discard("fake_rows")


def test_a_row_tool_over_mcp_logs_shape_and_masked_sql_only(out, monkeypatch):
    pytest.importorskip("mcp")
    from keygraph.mcp_server import build_server
    from tests.test_mcp import call
    srv = build_server(out)
    monkeypatch.setattr(devlog, "ROW_TOOLS", {*devlog.ROW_TOOLS, "fake_rows"})

    @srv.tool(name="fake_rows")
    def fake_rows(sql: str) -> dict:
        """Test double for the future run_query."""
        return {**ROWS, "rows": [["alice@example.com", i] for i in range(3000)], "row_count": 3000}
    assert main(["dev", "on", "--dir", str(out)]) == 0
    err, got = call(srv, "fake_rows", sql="SELECT email FROM shop.customers WHERE email = 'alice@example.com'")
    assert not err and len(got["rows"]) == 3000  # the agent still gets its rows
    ev = events(out)[-1]
    assert ev["tool"] == "fake_rows" and ev["response"]["row_count"] == 3000 and ev["ms"] >= 0
    assert "rows" not in ev["response"] and "response_ref" not in ev
    assert "alice" not in ev["args"]["sql"] and ev["args"]["sql"].startswith("SELECT email FROM shop.customers")
    assert "alice@example.com" not in (out / "dev" / "events.jsonl").read_text()


def test_devlog_decide_goes_to_collector_and_trace(tmp_path):
    dev_marker(tmp_path)
    with devlog.tracing(tmp_path, "t") as path, devlog.collecting() as dec:
        devlog.decide("x", a=1)
    assert dec == [{"kind": "x", "a": 1}] and read(path) == [{"a": 1, "kind": "x"}]
    with devlog.tracing(tmp_path, "outer") as outer, devlog.tracing(tmp_path, "inner") as inner:
        assert inner == outer
    assert len(traces(tmp_path)) == 2


def test_explain_on_a_column_ref_logs_only_the_final_resolve(demo_on):
    g = Graph.load(demo_on / "graph.json")
    with devlog.collecting() as lst:
        g.split_column("shop.orders.status")
    res = [d for d in lst if d["kind"] == "resolve"]
    assert [(d["name"], d["rule"]) for d in res] == [("shop.orders", "exact")]
    with devlog.collecting() as lst:
        g.split_column("shop.orders")
    assert [(d["name"], d["rule"]) for d in lst if d["kind"] == "resolve"] == [("shop.orders", "exact")]
    with devlog.collecting() as lst:
        with pytest.raises(Exception):  # noqa: B017, PT011
            g.split_column("shop.nothing_like_it.status")
    assert [d["rule"] for d in lst if d["kind"] == "resolve"] == ["unknown"]
    with devlog.collecting() as lst:
        with pytest.raises(Exception):  # noqa: B017, PT011
            g.split_column("nothing_like_it")
    assert [d["rule"] for d in lst if d["kind"] == "resolve"] == ["unknown"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_dev_dir_and_files_are_private(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    dev_marker(out)
    old = os.umask(0o022)
    try:
        assert devlog.Recorder(out).record("note", "dev_note", {"question": "q"})
        with devlog.tracing(out, "t") as p:
            devlog.trace("phase", name="x")
    finally:
        os.umask(old)
    mode = lambda p: stat.S_IMODE(p.stat().st_mode)  # noqa: E731
    assert mode(out / "dev") == 0o700 and mode(out / "dev" / "traces") == 0o700
    assert mode(out / "dev" / "events.jsonl") == 0o600 and mode(p) == 0o600
