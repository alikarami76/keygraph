import datetime as dt
import json

from keygraph import search as S
from keygraph.adapters import open_source
from keygraph.cli import main
from keygraph.context import ContextDB
from keygraph.graph import build_graph
from keygraph.adapters.live import scan_url
from keygraph.profile import build_sample_sql, profile_values, reconcile, run_profile, summarize

NOW = dt.datetime(2026, 1, 10)


def test_profile_basics():
    vals = ["new"] * 6 + ["paid"] * 3 + [None]
    p = profile_values(vals, "status", "varchar(10)", now=NOW)
    assert p["null_rate"] == 0.1 and p["distinct_in_sample"] == 2 and p["top_values"][0] == ["new", 6] and p["values_complete"]
    n = profile_values(list(range(1, 101)), "qty", "int", now=NOW)
    assert n["numeric"]["min"] == 1 and n["numeric"]["max"] == 100 and abs(n["numeric"]["p50"] - 50.5) < 1e-9
    d = profile_values([dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 8)], "created_at", "datetime", now=NOW)
    assert d["dates"]["recency_days"] == 2.0
    assert profile_values(['{"a": 1}'] * 3, "meta", "text", now=NOW)["pattern"] == "json"
    assert profile_values(["سلام دنیا"] * 3, "title", "text", now=NOW)["pattern"] == "persian_text"
    assert profile_values([120000, 50000], "final_price", "int", now=NOW)["pattern"] == "monetary"


def test_pii_never_stores_raw_values():
    for name, vals in (("email", ["a@x.com", "b@y.org"]), ("contact", ["a@x.com", "b@y.org", "c@z.io"]),
                       ("mobile", ["09121234567", "09351234567"]), ("first_name", ["Ali", "Sara"]),
                       ("national_id", [1234567890, 2345678901]), ("api_token", ["abc", "def"])):
        p = profile_values(vals, name, "varchar(50)", now=NOW)
        assert p["pii"] is True
        blob = json.dumps(p)
        assert "top_values" not in p and "numeric" not in p
        for v in vals:
            assert str(v) not in blob
    assert profile_values(["a@x.com"], "note", "text", pii=True, now=NOW).get("top_values") is None


def test_reconcile_flags_undocumented_and_unseen():
    p = profile_values([1, 1, 2, 9], "status", "int", now=NOW)
    r = reconcile(p, None, {"1": "new", "2": "paid", "3": "shipped"})
    assert r == {"undocumented_codes": ["9"], "unseen_codes": ["3"]}
    assert "UNDOCUMENTED" in summarize({**p, "reconcile": r})


def test_sample_sql_is_bounded():
    assert build_sample_sql("mysql", "`t`", ["a"], 100, "limit", quote="`").endswith("LIMIT 100")
    assert build_sample_sql("sqlserver", "t", ["a"], 100).startswith("SELECT TOP 100")
    assert "MAX(" in build_sample_sql("sqlite", "t", ["a"], 100, "recent", "id")


def test_end_to_end_sqlite_profile_search_and_cli(sqlite_url, tmp_path, capsys):
    import sqlite3
    path = sqlite_url.replace("sqlite:///", "")
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE tickets(id INTEGER PRIMARY KEY, state TEXT, email TEXT, amount INTEGER);
      INSERT INTO tickets VALUES (1,'open','a@x.com',10),(2,'open','b@x.com',20),(3,'blocked_by_vendor','c@x.com',30),(4,'closed','d@x.com',40);""")
    con.commit()
    con.close()
    g = build_graph(scan_url(sqlite_url))
    ctx = ContextDB(tmp_path / "k.db", g)
    ctx.index_graph(g)
    ctx.upsert("column", {"table": "main.tickets", "column": "state", "codes": {"open": "x", "closed": "y", "resolved": "z"}}, "t", False)
    src = open_source("sqlite", sqlite_url)
    rep = run_profile(g, src, ctx, tables=["tickets"], n=100, log=lambda *_: None)
    assert rep["profiled"] == 1 and rep["columns"] == 4
    p = g.data["profiles"]["main.tickets.state"]
    assert p["reconcile"]["undocumented_codes"] == ["blocked_by_vendor"] and p["reconcile"]["unseen_codes"] == ["resolved"]
    assert "top_values" not in g.data["profiles"]["main.tickets.email"] and "a@x.com" not in json.dumps(g.data["profiles"])
    assert any("blocked_by_vendor" in f for f in rep["findings"])
    ctx.reindex()
    r = S.search(g, ctx, "blocked_by_vendor")
    assert r["tables"][0]["table"] == "main.tickets" and "observed values" in r["tables"][0]["why"][0]
    assert ctx.load_profiles()["main.tickets.state"]["method"].startswith("recent")
    # skip over the row limit unless forced
    g.nodes["main.tickets"]["info"] = {"rows_estimate": 10**9}
    assert "main.tickets" in run_profile(g, src, None, tables=["tickets"], log=lambda *_: None)["skipped"]
    assert run_profile(g, src, None, tables=["tickets"], force=True, log=lambda *_: None)["profiled"] == 1
    # CLI
    out = tmp_path / "o"
    assert main(["scan", "--url", sqlite_url, "--dir", str(out)]) == 0
    assert main(["profile", "--source", "sqlite", "--url", sqlite_url, "--tables", "orders,products", "--dir", str(out)]) == 0
    assert main(["describe", "orders", "--dir", str(out)]) == 0
    text = capsys.readouterr().out
    assert "rows sampled" in text and "NOT NULL" in text or "rows sampled" in text
    assert main(["explain", "orders.customer_id", "--dir", str(out)]) == 0
    assert "values:" in capsys.readouterr().out
    html = (out / "keygraph.html").read_text()
    assert "<summary>Profile</summary>" in html


def test_probe_uses_range_overlap():
    from keygraph.probe import SelectOnlyRunner, probe_graph
    from keygraph.model import Schema, Table, Column, Index
    s = Schema()
    s.add(Table("m", "a", [Column("id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    s.add(Table("m", "b", [Column("id", "int"), Column("a_id", "int")], [Index("PRIMARY", ["id"], True, True)]))
    g = build_graph(s)
    g.data["profiles"] = {"m.b.a_id": {"numeric": {"min": 1000, "max": 2000}}, "m.a.id": {"numeric": {"min": 1, "max": 10}}}
    before = g.edges[0]["confidence"]
    probe_graph(g, SelectOnlyRunner(lambda sql: []), profiles=g.data["profiles"])
    assert g.edges[0]["confidence"] < before and "ranges do not overlap" in g.edges[0]["evidence"][-1]
