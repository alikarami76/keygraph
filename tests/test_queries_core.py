"""Query log building blocks: fingerprints, masking, linking roles and timing maths."""
from keygraph.code import sql as SQL
from keygraph.code.vocab import Vocab
from keygraph.queries.fingerprint import fingerprint, mask_sql, normalize
from keygraph.queries.link import link
from keygraph.queries.timing import percentile, timing

V = Vocab({"app.orders": ["id", "customer_id", "status", "placed_at"], "app.customers": ["id", "email"]})


def test_literal_changes_keep_the_fingerprint_structure_changes_do_not():
    a = fingerprint("SELECT o.id FROM orders o WHERE o.status = 3 AND o.placed_at > '2026-01-01' -- why")
    b = fingerprint("select o.id\nfrom orders o where o.status=7 and o.placed_at > '2026-02-01';")
    assert a == b and len(a[0]) == 16
    assert a[1] == "select o.id from orders o where o.status = ? and o.placed_at > ?"
    assert fingerprint("SELECT id FROM orders WHERE status = 1")[0] != fingerprint("SELECT id FROM orders WHERE total = 1")[0]


def test_in_lists_values_placeholders_and_quoted_identifiers():
    assert normalize("SELECT * FROM t WHERE id IN (1, 2, 3) AND x = :name AND y = ?") == \
        "select * from t where id in (?) and x = ? and y = ?"
    assert normalize("INSERT INTO t (a, b) VALUES (1, 'x'), (2, 'y')") == "insert into t (a, b) values (?, ?)"
    assert normalize('SELECT "Name" FROM `Shop`.t /* c */') == 'select "Name" from `Shop` . t'


def test_fingerprint_is_the_same_with_and_without_sqlglot(monkeypatch):
    sql = "SELECT c.id, COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id"
    with_lib = fingerprint(sql)
    monkeypatch.setattr(SQL, "sqlglot", None)
    assert fingerprint(sql) == with_lib


def test_stored_sql_masks_literals_and_secrets():
    sql = "SELECT * FROM users WHERE email = 'ana@example.com' AND id = 42 AND password = 'x'"
    assert mask_sql(sql) == "SELECT * FROM users WHERE email = ? AND id = ? AND password = ?"
    kept = mask_sql(sql, keep_literals=True)
    assert "ana@example.com" not in kept and "id = 42" in kept and "password = '***'" in kept


def test_link_roles_need_sqlglot(monkeypatch):
    sql = "SELECT c.email FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = 1 GROUP BY c.email"
    if SQL.sqlglot is not None:
        refs, unres = link(sql, V)
        assert ("app.orders", "status", "filter") in refs and ("app.orders", "customer_id", "join") in refs
        assert ("app.customers", "email", "group") in refs and ("app.customers", "email", "read") in refs
        assert ("app.orders", "", "read") in refs and unres == []
    monkeypatch.setattr(SQL, "sqlglot", None)
    refs, unres = link(sql + " UNION SELECT 1 FROM nowhere", V)
    assert refs == [("app.customers", "", "read"), ("app.orders", "", "read")] and unres == ["nowhere"]


def test_percentile_median_and_trend():
    assert percentile([], 0.95) is None and percentile([10.0], 0.95) == 10.0
    assert percentile([1, 2, 3, 4], 0.5) == 2.5 and percentile(list(range(1, 101)), 0.95) == 95.05
    t = timing([10, 20, 30, 40, 50])
    assert t == {"timed_runs": 5, "median_ms": 30, "p95_ms": 48.0, "trend": None}
    t = timing([10, 10, 100, 100, 100, 100, 100])
    assert t["trend"] == {"recent_median_ms": 100, "earlier_median_ms": 10, "change_pct": 900.0}


def test_comment_markers_inside_string_literals_do_not_truncate():
    a = fingerprint("select x from t where a = '--' and b = 1")
    assert a[1] == "select x from t where a = ? and b = ?"
    assert a[0] != fingerprint("select x from t where a = '--' and c = 1")[0]
    b = fingerprint("select x from t where a = '/*' and b = 1 /* real */")
    assert b[1] == "select x from t where a = ? and b = ?"
    assert b[0] == a[0]
    c = fingerprint("select x from t where a = 'q' -- real comment\n and b = 1")
    assert c[1] == "select x from t where a = ? and b = ?"
    assert fingerprint("select 1 from t where a = '#x'\n# c\n and b = 2")[1] == "select ? from t where a = ? and b = ?"


def test_mask_sql_keeps_the_tail_after_comment_markers_in_strings():
    assert mask_sql("select x from t where a = '--' and b = 1") == "select x from t where a = ? and b = ?"
