import pytest

from conftest import needs_sqlalchemy
from keygraph.adapters.live import scan_url
from keygraph.graph import build_graph
from keygraph.probe import SelectOnlyRunner, UnsafeSQL, assert_select, probe_graph
from keygraph.report import write_report
from keygraph.adapters.fetch import fetch_metadata, parse_rows


def test_report_sections(graph):
    r = write_report(graph)
    for h in ["Hub tables", "Tables with no relations", "Ambiguous or low-confidence relations", "Polymorphic columns",
              "Tables without a primary key", "Redundant indexes", "Foreign-key-like columns without an index"]:
        assert h in r
    assert "shop.scratch" in r and "shop.comments.commentable_id" in r and "ix_order" in r
    assert "shop.vendor_notes(vendor_id)" in r


@pytest.mark.parametrize("sql", ["DROP TABLE x", "SELECT 1; DELETE FROM x", "UPDATE x SET a=1", "SELECT * INTO OUTFILE 'f' FROM x",
                                 "WITH a AS (SELECT 1) DELETE FROM x", "SELECT 1 FOR UPDATE", "  ", "PRAGMA writable_schema=1"])
def test_probe_refuses_non_select(sql):
    with pytest.raises(UnsafeSQL):
        assert_select(sql)
    with pytest.raises(UnsafeSQL):
        SelectOnlyRunner(lambda s: []).run(sql)


def test_probe_allows_select_with_literals():
    assert assert_select("SELECT 'drop table' FROM t WHERE a IN (1,2);").startswith("SELECT")


@needs_sqlalchemy
def test_sqlite_scan_and_probe(sqlite_url):
    g = build_graph(scan_url(sqlite_url))
    assert g.edges and any(e["kind"] == "declared" for e in g.edges)
    e = next(e for e in g.edges if e["from_columns"] == ["order_id"])
    assert e["kind"] == "inferred"
    runner = SelectOnlyRunner.from_url(sqlite_url)
    res = probe_graph(g, runner, sample=100)
    assert res["probed"] >= 2 and not res["errors"]
    e = next(e for e in g.edges if e["from_columns"] == ["order_id"])
    assert e["match_rate"] == 0.75 and "probe:" in e["evidence"][-1]
    p = next(e for e in g.edges if e["from_columns"] == ["product_id"])
    assert p["match_rate"] == 0.667 or abs(p["match_rate"] - 2 / 3) < 0.01


def test_fetch_chunking():
    calls = []

    def run(sql):
        calls.append(sql)
        if "LIKE" not in sql:
            return [{"a": i} for i in range(3)]
        return [{"a": 1}]
    stats, kcu = fetch_metadata(run, "DATABASE()", limit=3)
    assert len(calls) > 2 and all(c.startswith("SELECT") for c in calls)
    assert parse_rows('log line\n{"rows": [{"x": 1}]}') == [{"x": 1}]


def test_assert_select_keeps_comment_markers_inside_literals():
    assert assert_select("SELECT a FROM t WHERE b = '--x' AND c = '/* y */'") == "SELECT a FROM t WHERE b = '--x' AND c = '/* y */'"
    assert assert_select("SELECT 1 /* c */ -- tail") == "SELECT 1"
    assert assert_select("SELECT '--' ; ").endswith("'--'")
    with pytest.raises(UnsafeSQL):
        assert_select("SELECT '--' /* ' */; DELETE FROM t")
    with pytest.raises(UnsafeSQL):
        assert_select("SELECT 'a' -- c\n; DROP TABLE t")


def test_sqlite_memory_url_is_a_clean_error(tmp_path, capsys):
    from keygraph.cli import main
    with pytest.raises(SystemExit) as e:
        main(["probe", "--dir", str(tmp_path), "--url", "sqlite://"])
    assert "sqlite URL needs a file path" in str(e.value.code)


@pytest.mark.parametrize("sql", [
    "SELECT \"a'b\" ; DROP TABLE t; SELECT 'c'",
    "SELECT `a'b` FROM t; DELETE FROM t WHERE x='1'",
    "SELECT 1 FROM t WHERE c=\"x'\" ; DELETE FROM t; SELECT '1'",
    "SELECT 'a' FROM t WHERE b = 'unterminated",
    "SELECT \"a FROM t",
    "SELECT `a FROM t",
    "SELECT \"--\" FROM t WHERE x = 1 DELETE FROM t",
    "SELECT \"a -- b\" FROM t WHERE 1=1 /* \" */ UNION SELECT 1; DROP TABLE t",
    "SELECT 'a;b' FROM t",
    "SELECT [a'b] FROM t; DROP TABLE t; SELECT '1'",
])
def test_assert_select_rejects_quote_confusion(sql):
    with pytest.raises(UnsafeSQL):
        assert_select(sql)


def test_assert_select_still_allows_quoted_markers_and_backslashes():
    assert assert_select("SELECT 'a -- b', '/* c */' FROM t") == "SELECT 'a -- b', '/* c */' FROM t"
    assert assert_select("SELECT 'a\\' FROM t") == "SELECT 'a\\' FROM t"
    assert assert_select('SELECT "a--b", `c/*d` FROM t -- tail') == 'SELECT "a--b", `c/*d` FROM t'
    with pytest.raises(UnsafeSQL):
        assert_select('SELECT "x--y" FROM t; DELETE FROM t')
