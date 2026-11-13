"""Dev mode report and export (stdlib), plus the leftover minors: bounded masking, decision masking and cap, safe tracing."""

import asyncio
import gzip
import json
import shutil
import time

# secret-shaped test strings are assembled at runtime so no secret-looking literal sits in the source
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
PW = "pass" + "word"

import pytest

from keygraph import devlog, devreport
from keygraph.cli import main
from tests.test_example_shop import _build


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("KEYGRAPH_DEV", raising=False)
    monkeypatch.delenv("KEYGRAPH_DEV_SESSION", raising=False)


def ev(seq, tool, session="s1", ts=None, kind="call", args=None, ms=1.0, ok=True, **kw):
    e = {"v": 1, "ts": ts or f"2026-11-12T10:00:{seq:02d}.000+00:00", "session": session, "seq": seq, "surface": "mcp",
         "client": {"name": "claude-code", "version": "2.0"}, "kind": kind, "tool": tool, "args": args or {}, "ms": ms, "ok": ok}
    e.update(kw)
    return e


def write(out, events, name="events.jsonl"):
    d = out / "dev"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, "a", encoding="utf-8") as f:
        f.writelines((e if isinstance(e, str) else json.dumps(e)) + "\n" for e in events)


def trace(out, name, events):
    d = out / "dev" / "traces"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("".join(json.dumps(e) + "\n" for e in events))


def synthetic(out):
    write(out, [ev(1, "dev_note", kind="note", args={"question": "late orders?"}),
                ev(2, "search", args={"query": "orders"}, ms=5, response={"tables": [{"table": "shop.orders"}], "join_paths": []}),
                ev(3, "search", args={"query": "zzz"}, ms=3, response={"tables": [], "join_paths": []}),
                ev(4, "describe", args={"table": "nosuch"}, ms=2, ok=False,
                   error={"type": "ToolError", "message": "unknown table ?"},
                   decisions=[{"kind": "resolve", "name": "nosuch", "rule": "unknown", "suggestions": ["shop.orders"]}]),
                ev(5, "describe", args={"table": "shop.orders"}, ms=40, response={"relations": [{"x": 1}]}),
                ev(6, "describe", args={"table": "shop.orders"}, ms=41, response={"relations": [{"x": 1}]}),
                ev(7, "keys", args={"table": "items"}, ok=False, error={"type": "ToolError", "message": "'?' is ambiguous"},
                   decisions=[{"kind": "resolve", "name": "items", "rule": "ambiguous", "candidates": ["a.items", "b.items"]}]),
                ev(8, "path", args={"from_table": "shop.orders", "to_table": "billing.payments"}, ms=9,
                   response={"paths": [{"steps": [{"from": "shop.orders", "to": "billing.payments", "ambiguous": True}]}]},
                   decisions=[{"kind": "path.rank", "winner_reason": "higher score (0.8075 vs 0.3822)"}]),
                ev(9, "path", args={"from_table": "a", "to_table": "b"}, response={"paths": []}),
                ev(10, "big", response_ref={"sha256": "x", "bytes": 80000, "tokens_approx": 20000, "shape": {}}),
                ev(11, "search", session="s2", args={"query": "x"}, response={"tables": [], "join_paths": []})])
    write(out, ["{not json", ""])
    write(out, [ev(0, "search", ts="2026-11-12T09:00:00.000+00:00", args={"query": "old"}, ms=1,
                   response={"tables": [{"table": "t"}]})], "events.1.jsonl")
    trace(out, "20261112T100000000000-load.jsonl", [
        {"kind": "phase", "name": "build_graph", "ms": 3.5, "tables": 10},
        {"kind": "infer.column", "table": "a.t", "column": "x_id", "outcome": "kept", "candidates": [
            {"outcome": "kept", "reasons": ["column name 'x_id' matches table name"], "to": "a.x"},
            {"outcome": "rejected", "reason": "same-schema candidate preferred over cross-schema ones",
             "reasons": ["column name 'x_id' matches table name"], "to": "r.x"}]},
        {"kind": "infer.column", "table": "a.t", "column": "q_id", "outcome": "unresolved"},
        {"kind": "mirror.decision", "original": "a", "copy": "r", "verdict": "mirror", "reason": "keys"},
        {"kind": "hub.decision", "table": "a.u", "incoming": 9, "verdict": "picked", "reason": "x"},
        {"kind": "truncated", "events": 5}])


# ---- report -----------------------------------------------------------------------------------------------

def test_empty_and_cleared_logs_say_no_events(tmp_path, capsys):
    r = devreport.report(tmp_path)
    assert r["overview"]["events"] == 0 and "no events" in devreport.format_report(r)
    assert main(["dev", "report", "--dir", str(tmp_path)]) == 0
    assert "no events" in capsys.readouterr().out
    assert main(["dev", "report", "--json", "--dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["overview"]["events"] == 0
    f = tmp_path / "x.jsonl"
    assert main(["dev", "export", str(f), "--dir", str(tmp_path)]) == 0
    assert "no events" in capsys.readouterr().out and not f.exists()
    synthetic(tmp_path)
    assert main(["dev", "clear", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert devreport.report(tmp_path)["overview"]["events"] == 0


def test_every_section_from_a_synthetic_log(tmp_path):
    synthetic(tmp_path)
    r = devreport.report(tmp_path)
    o = r["overview"]
    assert o["events"] == 12 and o["corrupt_lines"] == 1 and o["sessions"] == 2 and o["surfaces"] == {"mcp": 12}
    assert o["period"] == {"first": "2026-11-12T09:00:00.000+00:00", "last": "2026-11-12T10:00:11.000+00:00"}
    assert o["clients"] == [{"name": "claude-code", "version": "2.0", "events": 12}]
    use = {u["tool"]: u for u in r["usage"]}
    assert use["search"]["count"] == 4 and use["search"]["share"] == round(400 / 11, 1)  # the note is not a tool call
    assert "dev_note" not in use
    lat = {x["tool"]: x for x in r["latency"]["per_tool"]}
    assert lat["describe"]["max"] == 41 and lat["describe"]["p50"] == 40 and lat["describe"]["p95"] == 41
    assert r["latency"]["slowest"][0]["tool"] == "describe" and r["latency"]["slowest"][0]["ms"] == 41
    assert {(e["tool"], e["type"], e["message"], e["count"]) for e in r["errors"]} == {
        ("describe", "ToolError", "unknown table ?", 1), ("keys", "ToolError", "'?' is ambiguous", 1)}
    emp = {e["tool"]: e for e in r["empty"]}
    assert emp["search"]["count"] == 2 and emp["path"]["count"] == 1 and emp["search"]["examples"]
    amb = r["ambiguous"]
    assert amb["resolve"][0]["candidates"] == ["a.items", "b.items"] and amb["resolve"][0]["rule"] == "ambiguous"
    assert amb["paths"][0]["edges"] == [{"from": "shop.orders", "to": "billing.payments"}]
    rep = r["repeated"]
    assert rep["same_args"][0]["tool"] == "describe" and rep["same_args"][0]["count"] == 2
    assert rep["retries"][0]["tool"] == "describe" and rep["retries"][0]["retry_ok"] is True
    size = {s["tool"]: s for s in r["size"]["per_tool"]}
    assert size["big"]["max"] == 20000 and r["size"]["unit"] == "approx tokens = chars/4"
    (n,) = r["notes"]
    assert n["question"] == "late orders?" and n["call_count"] == 9 and n["calls"][0]["tool"] == "search"
    b = r["build"]
    assert b["trace"] == "20261112T100000000000-load" and b["truncated"] is True and b["unresolved_columns"] == 1
    assert b["phases"] == [{"name": "build_graph", "ms": 3.5, "tables": 10}]
    assert b["candidates"]["kept"] == {"column name '…' matches table name": 1}
    assert b["candidates"]["rejected"] == {"same-schema candidate preferred over cross-schema ones": 1}
    assert b["mirror"][0]["verdict"] == "mirror" and b["hubs"][0]["table"] == "a.u"
    assert r["decisions"]["resolve_rules"] == {"ambiguous": 1, "unknown": 1}
    assert r["decisions"]["path_winner_reasons"] == {"higher score": 1}
    md = devreport.format_report(r)
    for h in ("## 1. Overview", "## 2. Most-used tools", "## 3. Latency per tool", "## 4. Errors", "## 5. Empty answers",
              "## 6. Ambiguous answers", "## 7. Repeated calls", "## 8. Response size", "## 9. Agent notes", "## 10. Latest build",
              "Decisions seen at query time", "approx tokens = chars/4"):
        assert h in md


def test_report_is_deterministic_and_has_no_clock(tmp_path):
    synthetic(tmp_path)
    a, b = devreport.report(tmp_path), devreport.report(tmp_path)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert devreport.format_report(a) == devreport.format_report(b)
    assert "generated" not in devreport.format_report(a).lower()


def test_since_and_session_filters(tmp_path, capsys):
    synthetic(tmp_path)
    r = devreport.report(tmp_path, since="2026-11-12T10:00:00")
    assert r["overview"]["events"] == 11 and r["overview"]["period"]["first"].startswith("2026-11-12T10:00:01")
    assert devreport.report(tmp_path, session="s2")["overview"]["events"] == 1
    assert devreport.report(tmp_path, since="2027-01-01")["overview"]["events"] == 0
    assert main(["dev", "report", "--json", "--since", "2026-11-12", "--session", "s2", "--dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["overview"]["events"] == 1
    with pytest.raises(ValueError):
        devreport.report(tmp_path, since="last week")


def test_report_tolerates_odd_events(tmp_path):
    write(tmp_path, ["[1, 2]", "42", json.dumps({"kind": "call"}), json.dumps(ev(1, "search", response="text", ms="x")),
                     json.dumps(ev(2, "search", error="boom", ok=False))])
    r = devreport.report(tmp_path)
    assert r["overview"]["events"] >= 2 and "## 1. Overview" in devreport.format_report(r)


def test_cli_report_markdown(tmp_path, capsys):
    synthetic(tmp_path)
    assert main(["dev", "report", "--dir", str(tmp_path)]) == 0
    assert "## 4. Errors" in capsys.readouterr().out


# ---- export -----------------------------------------------------------------------------------------------

def test_export_tags_every_line_and_warns(tmp_path, capsys):
    synthetic(tmp_path)
    f = tmp_path / "x.jsonl"
    assert main(["dev", "export", str(f), "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "table names" in out and "share it with care" in out
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    from keygraph import __version__
    assert lines[0] == {"keygraph_dev_export": 1, "keygraph": __version__,
                        "files": ["events.1.jsonl", "events.jsonl", "traces/20261112T100000000000-load.jsonl"]}
    body = lines[1:]
    assert [x["file"] for x in body][:2] == ["events.1.jsonl", "events.jsonl"] and body[-1]["file"].startswith("traces/")
    assert len(body) == 1 + 13 + 6 - 1  # old + live lines (the blank one is skipped, the corrupt one kept) + trace
    assert any("raw" in x for x in body) and body[1]["event"]["tool"] == "dev_note"
    g = tmp_path / "x.jsonl.gz"
    assert main(["dev", "export", str(g), "--since", "2026-11-12T10:00:00", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    with gzip.open(g, "rt", encoding="utf-8") as zf:
        gl = [json.loads(x) for x in zf.read().splitlines()]
    assert gl[0]["keygraph_dev_export"] == 1 and all(x["file"] != "events.1.jsonl" for x in gl[1:])
    # byte-identical twice (no clock in the gzip header)
    h = tmp_path / "y.jsonl.gz"
    main(["dev", "export", str(h), "--since", "2026-11-12T10:00:00", "--dir", str(tmp_path)])
    assert g.read_bytes() == h.read_bytes()


# ---- minors -----------------------------------------------------------------------------------------------

def test_mask_secrets_is_linear_on_one_long_token():
    from keygraph.code.refs import mask_secrets
    for s in ("a" * 20000, "a." * 10000, "a-" * 10000, "a " * 10000):
        t0 = time.perf_counter()
        mask_secrets(s)
        assert time.perf_counter() - t0 < 0.5
    assert mask_secrets("api_key = 'abc123'") == "api_key = '***'"
    assert mask_secrets(f"db.{PW}: 'hunter2'") == "db.password: '***'"


def test_record_masks_decisions_and_resolve_names(tmp_path):
    rec = devlog.Recorder(tmp_path)
    secret = PW + "=hunter2hunter2"
    assert rec.record("call", "x", decisions=[{"kind": "note", "text": f"see {secret} at http://u:p@h/x", "n": 3}])
    d = json.loads((tmp_path / "dev" / "events.jsonl").read_text())["decisions"][0]
    assert "hunter2" not in json.dumps(d) and d["n"] == 3 and "u:p" not in json.dumps(d)
    with devlog.collecting() as lst:
        devlog.decide("resolve", name=AWS_KEY, rule="unknown", suggestions=[])
        devlog.decide("resolve", name="shop.orders", rule="exact", table="shop.orders")
    assert "AKIA" not in lst[0]["name"] and lst[1]["name"] == "shop.orders"


def test_decisions_cap_keeps_200_and_marks_the_rest():
    with devlog.collecting() as lst:
        for i in range(250):
            devlog.decide("x", i=i)
    assert len(lst) == 201 and lst[199]["i"] == 199 and lst[200] == {"kind": "truncated", "dropped": 50}
    with devlog.collecting() as few:
        for i in range(200):
            devlog.decide("x", i=i)
    assert len(few) == 200 and few[-1]["kind"] == "x"


def test_record_caps_a_long_decisions_list(tmp_path):
    devlog.Recorder(tmp_path).record("call", "x", decisions=[{"kind": "k", "i": i} for i in range(230)])
    d = json.loads((tmp_path / "dev" / "events.jsonl").read_text())["decisions"]
    assert len(d) == 201 and d[-1] == {"kind": "truncated", "dropped": 30}


def test_rows_shape_masks_sql_literals_in_warnings_and_checks():
    s = devlog.rows_shape({"rows": [], "warnings": ["value 'Ali K' not found", "id = 12345678 missing"],
                           "checks": {"a": "name = 'Bob' ok", "n": 3}})
    assert "Ali" not in json.dumps(s) and "Bob" not in json.dumps(s) and "12345678" not in json.dumps(s)
    assert s["checks"]["n"] == 3


def test_dev_note_hint_claims_grouping_only_when_noted(tmp_path):
    on = tmp_path / "on"
    (on / "dev").mkdir(parents=True)
    (on / "dev" / "enabled").write_text("")
    r = devlog.note(on, "q", "mcp")
    assert r["noted"] and "grouped" in r["hint"]
    r = devlog.note(tmp_path / "off", "q", "mcp")
    assert not r["noted"] and "grouped" not in r["hint"]


def test_path_ranking_reason_shows_enough_precision():
    from keygraph import query as Q
    res = [{"hops": 2, "tables": ["a", "b"], "confidence": 1.0}, {"hops": 2, "tables": ["a", "c"], "confidence": 1.0}]
    seen = []
    orig = devlog.decide
    try:
        devlog.decide = lambda kind, **f: seen.append(f)
        Q._trace_paths(res, [0.500012, 0.500011], [0, 1], [((), 0), ((), 0)], set(), None, "b", 5, 10, 3, {})
    finally:
        devlog.decide = orig
    reason = seen[0]["winner_reason"]
    assert reason.startswith("higher score") and "0.500012" in reason and "0.500011" in reason


def test_trace_helper_failure_never_breaks_a_tool(tmp_path, monkeypatch):
    from keygraph import query as Q
    from keygraph.cli import run_pipeline
    from keygraph.graph import Graph
    from keygraph.model import Column, Index, Schema, Table
    pk = [Index("PRIMARY", ["id"], True, True)]
    s = Schema()
    s.add(Table("a", "t", [Column("id", "int")], list(pk)))
    s.add(Table("a", "u", [Column("id", "int"), Column("t_id", "int")], list(pk)))
    run_pipeline(s, tmp_path, verbose=False)
    g = Graph.load(tmp_path / "graph.json")
    want = Q.find_paths(g, "a.u", "a.t")
    monkeypatch.setattr(Q, "_trace_paths", lambda *a, **k: 1 / 0)
    with devlog.collecting():
        assert Q.find_paths(g, "a.u", "a.t") == want
    assert want


def test_path_candidates_hold_a_copy_of_the_tables_list(tmp_path):
    from keygraph import query as Q
    from keygraph.cli import run_pipeline
    from keygraph.graph import Graph
    from keygraph.model import Column, Index, Schema, Table
    pk = [Index("PRIMARY", ["id"], True, True)]
    s = Schema()
    s.add(Table("a", "t", [Column("id", "int")], list(pk)))
    s.add(Table("a", "u", [Column("id", "int"), Column("t_id", "int")], list(pk)))
    run_pipeline(s, tmp_path, verbose=False)
    g = Graph.load(tmp_path / "graph.json")
    with devlog.collecting() as lst:
        res = Q.find_paths(g, "a.u", "a.t")
    res[0]["tables"].append("mutated")
    cand = next(d for d in lst if d["kind"] == "path.rank")["candidates"][0]
    assert "mutated" not in cand["tables"]


# ---- MCP: params logged, scripted session -----------------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("devreport_shop")
    _build(d, profile=True)
    return d


def _srv(built, tmp_path, fresh=False):
    pytest.importorskip("mcp")
    from keygraph.mcp_server import build_server
    out = tmp_path / "o"
    if fresh:  # build with dev mode on, so the dir holds a build trace
        (out / "dev").mkdir(parents=True)
        (out / "dev" / "enabled").write_text("")
        _build(out, profile=True)
    else:
        shutil.copytree(built, out)
        assert main(["dev", "on", "--dir", str(out)]) == 0
    return out, build_server(out)


def _call(srv, tool, **args):
    try:
        return asyncio.run(srv.call_tool(tool, args))
    except Exception as exc:  # noqa: BLE001  (tool errors are part of the session)
        return exc


def _events(out):
    return [json.loads(x) for x in (out / "dev" / "events.jsonl").read_text().splitlines()]


def test_brief_and_with_paths_args_are_logged(built, tmp_path):
    out, srv = _srv(built, tmp_path)
    _call(srv, "describe", table="shop.orders", brief=True)
    _call(srv, "search", query="orders", with_paths=False)
    d, s = _events(out)
    assert d["args"] == {"brief": True, "table": "shop.orders"} and s["args"] == {"query": "orders", "with_paths": False}


def test_scripted_session_triggers_every_section(built, tmp_path, monkeypatch, capsys):
    out, srv = _srv(built, tmp_path, fresh=True)
    _call(srv, "dev_note", question="which orders are late?")
    _call(srv, "search", query="orders")
    _call(srv, "search", query="zzzqqq nothing")
    _call(srv, "describe", table="shop.orders", brief=True)
    _call(srv, "describe", table="customers")  # original over mirror
    _call(srv, "describe", table="nosuch")  # unknown table, then a retry with a name that exists
    _call(srv, "describe", table="shop.customers")
    _call(srv, "path", from_table="shop.orders", to_table="billing.payments")  # the ambiguous edge
    _call(srv, "path", from_table="shop.orders", to_table="billing.payments")  # repeated
    _call(srv, "neighbors", table="shop.orders")
    _call(srv, "query_search", text="zzzz")  # nothing found
    with monkeypatch.context() as m:
        m.setattr(devlog, "INLINE_MAX", 200)
        _call(srv, "describe", table="shop.users")  # large response stored as a reference
    r1, r2 = devreport.report(out), devreport.report(out)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    md1, md2 = devreport.format_report(r1), devreport.format_report(r2)
    assert md1 == md2
    assert r1["overview"]["events"] >= 12 and r1["usage"] and r1["latency"]["slowest"] and r1["errors"]
    assert {e["tool"] for e in r1["empty"]} >= {"search", "query_search"}
    assert r1["ambiguous"]["paths"] and r1["repeated"]["same_args"] and r1["repeated"]["retries"]
    assert r1["size"]["per_tool"] and r1["notes"][0]["call_count"] >= 10
    assert r1["decisions"]["resolve_rules"].get("original_over_mirror_or_copy")
    assert r1["build"]["trace"] and r1["build"]["phases"] and r1["build"]["candidates"]["rejected"]
    for n in range(1, 11):
        title = md1.split(f"## {n}. ")[1].split("\n## ")[0]
        assert len(title.strip().splitlines()) > 1, n
    capsys.readouterr()


# ---- fix round 1 ------------------------------------------------------------------------------------------

def test_unicode_line_separators_inside_an_event_do_not_split_it(tmp_path):
    write(tmp_path, [ev(1, "search", args={"query": "a b c\u0085d"}), ev(2, "keys")])
    r = devreport.report(tmp_path)
    assert r["overview"]["events"] == 2 and r["overview"]["corrupt_lines"] == 0
    f = tmp_path / "x.jsonl"
    devreport.export(tmp_path, f)
    assert [json.loads(x)["line"] for x in f.read_text(encoding="utf-8").split("\n")[1:3]] == [1, 2]


def test_since_accepts_iso_forms_and_offsets(tmp_path):
    write(tmp_path, [ev(1, "a", ts="2026-11-13T08:59:00.000+00:00"), ev(2, "b", ts="2026-11-13T09:30:00.000+00:00"),
                     ev(3, "c", ts="2026-11-14T00:00:00.000+00:00")])
    trace(tmp_path, "20261113T085000000000-load.jsonl", [{"kind": "phase", "name": "p", "ms": 1}])
    trace(tmp_path, "20261113T093500000000-load.jsonl", [{"kind": "phase", "name": "q", "ms": 1}])

    def n(s):
        return devreport.report(tmp_path, since=s)["overview"]["events"]
    assert n("20261113") == 3 and n("2026-11-14") == 1 and n("2026-11-13") == 3
    assert n("2026-11-13 09:00") == 2 and n("2026-11-13T09:00") == 2 and n("2026-11-13T09:30:00") == 2
    assert n("2026-11-13T12:30+03:30") == 2      # 09:00 UTC
    assert n("2026-11-13T09:31Z") == 1
    assert n("2026-11-13T13:00+03:30") == 2      # 09:30 UTC exactly keeps event b and c
    assert devreport.report(tmp_path, since="2026-11-13 09:00")["overview"]["trace_files"] == 1
    assert devreport.report(tmp_path, since="2026-11-13T13:00+03:30")["overview"]["trace_files"] == 1  # 09:30 UTC
    assert devreport.report(tmp_path, since="2026-11-13T11:00+03:30")["overview"]["trace_files"] == 2  # 07:30 UTC


def _old_mask(text):
    """mask_secrets as it was at 8ac284b (the same code but the original _EMAIL and _KV regexes)."""
    import re

    from keygraph.code import refs
    email = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
    kvr = re.compile(r"""(?P<k>[A-Za-z_$][\w$.\-]*)(?P<q0>['"]?)\s*(?P<op>=>|:=|==|=|:)\s*(?P<q>['"])(?P<v>[^'"\n]*)(?P=q)""")
    text = refs._URL_CRED.sub(r"\1***@", text)
    text = email.sub("***@***", text)

    def kv(m):
        key = m.group("k").rsplit(".", 1)[-1].lstrip("$")
        if not m.group("v") or not refs.is_pii_name(refs._snake(key)):
            return m.group(0)
        whole, base = m.group(0), m.start()
        return whole[:m.start("v") - base] + "***" + whole[m.end("v") - base:]
    text = kvr.sub(kv, text)

    def plain(m):
        import re as _re
        if (m.group("k").lower() in ("pass", "tokens") or not refs._secret_key(m.group("k"))
                or _re.match(r"[\w$]+\.[\w$]", m.group("v")) or refs._CODE_VALUE.match(m.group("v"))):
            return m.group(0)
        return m.group(0)[:m.start("k") - m.start()] + m.group("k") + m.group("sep") + "***"
    return refs._LONG_TOKEN.sub("***", refs._PLAIN_KV.sub(plain, text))


def test_mask_secrets_matches_the_old_output_on_tricky_strings():
    import random

    from keygraph.code.refs import mask_secrets
    fixed = ["a1@b.comc2@d.org", "x@x@x@y.com", "+a@x.com", "..a@x.io", "a.b+c@x-y.co.uk", "u@host", "http://u:p@h/x",
             '"password' + "_" * 100 + '": "zzz"', '"' + "x" * 120 + 'password": "zzz"', "password" + "k" * 130 + "='abc'",
             "1abc='x'", "éabc: 'tok'", "api_key = 'abc123'", f"db.{PW}: 'hunter2'", "$token => 'q'", "a@b.c", "a@b.co@c.de",
             "email: 'a@b.com' password:'p' token=\"t\""]
    rng = random.Random(7)
    parts = ["a", "1", "@", ".", "=", ":", " ", "'", '"', "_", "-", "password", "token", "x.com", "email", "=>", "$", "é", "q@w.io"]
    corpus = fixed + ["".join(rng.choice(parts) for _ in range(rng.randint(1, 25))) for _ in range(4000)]
    for s in corpus:
        assert mask_secrets(s) == _old_mask(s), s


def test_glued_emails_and_long_secret_keys_are_still_masked():
    from keygraph.code.refs import mask_secrets
    assert mask_secrets("a1@b.comc2@d.org") == "***@******@***"
    assert mask_secrets('"password' + "_" * 100 + '": "zzz"').endswith('": "***"')


def test_typed_names_are_kept_and_token_shapes_masked():
    names = ["Order Items", "tmp$orders", "`shop`.`orders`", "فروش.سفارش", "my-schema.my-table"]
    with devlog.collecting() as lst:
        for n in names:
            devlog.decide("resolve", name=n, rule="unknown", suggestions=[])
    assert [d["name"] for d in lst] == names
    for secret in (AWS_KEY, "ghp_" + "a1B2" * 9, "github_pat_" + "x1" * 20, "gho_" + "Z9" * 15,
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"):
        assert secret not in devlog.mask_text(f"table {secret} not found")
        with devlog.collecting() as lst:
            devlog.decide("resolve", name=secret, rule="unknown", suggestions=[])
        assert secret not in json.dumps(lst)
    assert devlog.mask_text("billing.payments.transaction_reference") == "billing.payments.transaction_reference"


def test_export_masks_corrupt_lines_and_refuses_targets_inside_dev(tmp_path):
    write(tmp_path, [ev(1, "x"), '{"truncated": "' + AWS_KEY + " " + PW + "=hunter2hunter2"])
    f = tmp_path / "x.jsonl"
    devreport.export(tmp_path, f)
    text = f.read_text()
    assert AWS_KEY not in text and "hunter2" not in text and '"raw"' in text
    for bad in (tmp_path / "dev" / "x.jsonl", tmp_path / "dev" / "traces" / "x.jsonl", tmp_path / "dev"):
        with pytest.raises(ValueError, match="dev"):
            devreport.export(tmp_path, bad)
    with pytest.raises(ValueError):
        devreport.export(tmp_path, tmp_path)  # a directory
    assert main(["dev", "export", str(tmp_path / "dev" / "e.gz"), "--dir", str(tmp_path)]) == 2


def test_text_mode_cli_answers_are_declared_unclassified(tmp_path):
    synthetic(tmp_path)
    md = devreport.format_report(devreport.report(tmp_path))
    assert "not classified" in md.split("## 5. ")[1].split("\n## ")[0]


def test_reason_grouping_handles_apostrophes_unicode_and_missing_reasons(tmp_path):
    trace(tmp_path, "20261113T100000000000-load.jsonl", [
        {"kind": "infer.column", "table": "a.t", "column": "c", "outcome": "kept", "candidates": [
            {"outcome": "kept", "reasons": ["column name 'x' matches table name with this table's prefix 'order_'"], "to": "a.x"},
            {"reasons": ["role of فروش.سفارش"], "to": "a.y"}]}])
    write(tmp_path, [ev(1, "search")])
    c = devreport.report(tmp_path)["build"]["candidates"]
    assert list(c["kept"]) == ["column name '…' matches table name with this table's prefix '…'"]
    assert c["rejected"] == {"(no reason recorded)": 1} and list(c["rejected_heuristics"]) == ["role of <table>"]


def test_since_with_out_of_range_offset_is_a_friendly_error(tmp_path, capsys):
    synthetic(tmp_path)
    for bad in ("9999-12-31T23:59-05:00", "0001-01-01T00:00+05:00"):
        with pytest.raises(ValueError, match="--since"):
            devreport.report(tmp_path, since=bad)
        assert main(["dev", "report", "--since", bad, "--dir", str(tmp_path)]) == 2
        capsys.readouterr()


def test_mask_secrets_keeps_code_that_only_looks_like_a_secret_assignment():
    from keygraph.code.refs import mask_secrets
    keep = ["_LOG_TOKENS = frozenset(LOG_HINTS)", "tokens: [1, 2]", "tokens: int,", "# second pass: uses over files", "UNIT = \"approx tokens = chars/4\"", "TOKENS = {'a': 1}", "token = settings.TOKEN",
            "self.token = make_token(x)", "auth = (user, pw)", "token = MAX_TOKENS", "tokens: {a}"]
    for line in keep:
        assert mask_secrets(line) == line, line
    for line in ("password=hunter2", "db_password: x", "api_key=abc123", "DB_PASS=x", "client_secret=s3cr3t"):
        assert "***" in mask_secrets(line), line


def test_github_token_shape_needs_a_digit_so_ordinary_names_stay():
    assert devlog.mask_text("table github_pat_assignments_per_team missing") == "table github_pat_assignments_per_team missing"
    assert devlog.mask_text("ghp_" + "abcdefghij" * 3) == "ghp_" + "abcdefghij" * 3
    assert "<token>" in devlog.mask_text("x " + "github_" + "pat_11ABCDEFG0abcdefghijkl_mnopqrstuv y")


def cli_ev(seq, ts, session, source="ppid", **kw):
    kw.setdefault("kind", "command")
    e = ev(seq, "search", session=session, ts=ts, surface="cli", **kw)
    if source:
        e["session_source"] = source
    return e


def test_cli_ppid_sessions_group_by_idle_gap(tmp_path):
    write(tmp_path, [cli_ev(1, f"2026-11-12T10:0{i}:00.000+00:00", f"ppid{i}", args={"q": i}) for i in range(6)])
    r = devreport.report(tmp_path)
    assert r["overview"]["sessions"] == 1
    sid = next(iter({x["session"] for x in r["latency"]["slowest"]}))
    assert sid.startswith("cli-")
    # a note joins the regrouped session
    write(tmp_path, [cli_ev(1, "2026-11-12T10:06:30.000+00:00", "ppidN", kind="note", args={"question": "q"})])
    assert devreport.report(tmp_path)["notes"][0]["call_count"] == 0
    write(tmp_path, [cli_ev(2, "2026-11-12T10:07:00.000+00:00", "ppidM")])
    n = devreport.report(tmp_path)["notes"][0]
    assert n["call_count"] == 1 and n["session"] == sid


def test_cli_ppid_sessions_split_after_ten_idle_minutes(tmp_path):
    write(tmp_path, [cli_ev(1, "2026-11-12T10:00:00.000+00:00", "a"), cli_ev(1, "2026-11-12T10:10:00.000+00:00", "b"),
                     cli_ev(1, "2026-11-12T10:21:01.000+00:00", "c"), cli_ev(1, "2026-11-12T10:30:00.000+00:00", "d")])
    assert devreport.report(tmp_path)["overview"]["sessions"] == 2  # 10:10 is within the gap, 10:21:01 is 11:01 later
    assert devreport.IDLE_GAP.total_seconds() == 600


def test_cli_regrouping_leaves_env_mcp_and_explicit_sessions_alone(tmp_path):
    write(tmp_path, [cli_ev(1, "2026-11-12T10:00:00.000+00:00", "mine", source="env"),
                     cli_ev(1, "2026-11-12T10:01:00.000+00:00", "other", source="env"),
                     ev(1, "search", session="m1", ts="2026-11-12T10:02:00.000+00:00"),
                     cli_ev(1, "2026-11-12T10:03:00.000+00:00", "p1"), cli_ev(1, "2026-11-12T10:04:00.000+00:00", "p2")])
    o = devreport.report(tmp_path)["overview"]
    assert o["sessions"] == 4  # mine, other, m1, one regrouped CLI session
    assert devreport.report(tmp_path, session="mine")["overview"]["events"] == 1


def test_older_cli_events_without_session_source_are_regrouped_and_ids_are_stable(tmp_path):
    write(tmp_path, [cli_ev(1, f"2026-11-12T10:0{i}:00.000+00:00", f"x{i}", source=None) for i in range(4)])
    a = devreport.format_report(devreport.report(tmp_path))
    b = devreport.format_report(devreport.report(tmp_path))
    assert a == b and devreport.report(tmp_path)["overview"]["sessions"] == 1
    sid = next(iter({x["session"] for x in devreport.report(tmp_path)["latency"]["slowest"]}))
    assert devreport.report(tmp_path, session=sid)["overview"]["events"] == 4


def test_real_cli_commands_record_the_session_source(tmp_path, monkeypatch):
    main(["dev", "on", "--dir", str(tmp_path)])
    monkeypatch.setenv("KEYGRAPH_DEV_SESSION", "mine")
    main(["dev", "note", "q", "--dir", str(tmp_path)])
    monkeypatch.delenv("KEYGRAPH_DEV_SESSION")
    main(["dev", "note", "q2", "--dir", str(tmp_path)])
    srcs = [json.loads(x)["session_source"] for x in (tmp_path / "dev" / "events.jsonl").read_text().splitlines()]
    assert srcs == ["env", "ppid"]
    assert devlog.session_source("mcp") == "mcp"
