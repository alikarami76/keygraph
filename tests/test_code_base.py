"""Code index foundations: naming variants, vocabulary, file walk, line index, snippet masking and the store."""
import os

import pytest

from keygraph.code.refs import CodeRef, Unresolved, mask_secrets, snippet
from keygraph.code.store import CodeStore
from keygraph.code.vocab import COMMON, Vocab, camel, pascal, snake, variants
from keygraph.code.walk import Lines, read, walk


def test_naming_variants():
    assert snake("customerId") == "customer_id" and snake("HTTPCode") == "http_code" and snake("order_id") == "order_id"
    assert camel("customer_id") == "customerId" and pascal("customer_id") == "CustomerId"
    assert variants("status") == ["status", "Status", "getStatus", "setStatus", "isStatus", "hasStatus"]
    assert variants("trackingNo")[:4] == ["trackingNo", "tracking_no", "TrackingNo", "getTrackingNo"]
    assert {"id", "status", "created_at"} <= COMMON


def test_vocab_resolves_names_and_prefers_real_tables_over_mirrors():
    v = Vocab({"shop.orders": ["id", "customer_id"], "reporting.orders": ["id"], "billing.payments": ["id", "order_id"]},
              mirrors={"reporting.orders"})
    assert v.resolve_table("orders") == ["shop.orders"]
    assert v.resolve_table("`reporting`.`orders`") == ["reporting.orders"]
    assert v.resolve_table("BILLING.payments") == ["billing.payments"]
    assert v.resolve_table("nope") == []
    assert v.column("shop.orders", "customerId") == "customer_id"
    assert v.column("shop.orders", "customer") == "customer_id"  # a relation property maps to its _id column
    assert v.column("shop.orders", "total") is None
    assert v.tables_with_column("order_id") == ["billing.payments"]
    both = Vocab({"a.users": ["id"], "b.users": ["id"]})
    assert both.resolve_table("users") == ["a.users", "b.users"]


def test_walk_skips_vendor_binary_minified_big_excluded_and_symlinks(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "src" / "b.TS").write_text("let x = 1\n")
    (tmp_path / "src" / "notes.txt").write_text("SELECT 1 FROM t\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("x\n")
    (tmp_path / "app.min.js").write_text("x\n")
    (tmp_path / "blob.php").write_bytes(b"<?php\x00\x01")
    (tmp_path / "big.sql").write_text("-- " + "x" * 3000)
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "c.go").write_text("package x\n")
    os.symlink(tmp_path / "src" / "a.py", tmp_path / "link.py")
    files = walk(tmp_path, exclude=["gen/*"], max_file_kb=2)
    assert [(f.path, f.lang) for f in files] == [("blob.php", "php"), ("src/a.py", "py"), ("src/b.TS", "ts")]
    assert read(files[0]) is None  # NUL byte: binary
    sha, text = read(files[1])
    assert len(sha) == 40 and text == "x = 1\n"


def test_lines():
    L = Lines("a\nbb\n\nccc")
    assert [L.line(o) for o in (0, 1, 2, 4, 5, 6)] == [1, 1, 2, 2, 3, 4]
    assert L.get(2) == "bb" and L.get(3) == "" and L.get(4) == "ccc"


def test_snippets_mask_secrets_and_trim():
    s = mask_secrets("db = connect(password='hunter2', user=\"ana@example.com\", api_key: \"k-123\")")
    assert "hunter2" not in s and "ana@example.com" not in s and "k-123" not in s
    assert "password='***'" in s
    assert mask_secrets("url = 'mysql://app:s3cret@db.local/shop'") == "url = 'mysql://***@db.local/shop'"
    assert "***" in mask_secrets("token = abcdefghijklmnopqrstuvwxyz0123456789ABCD")
    assert mask_secrets("status = 'paid'") == "status = 'paid'"
    assert len(snippet("    " + "x" * 400)) == 160 and snippet("  a  ") == "a"


def test_store_round_trip_and_readonly_on_missing_file(tmp_path):
    db = tmp_path / "keygraph.db"
    with CodeStore(db, readonly=True) as st:
        assert st.stats() == {"repos": []} and st.where("app.orders") == []
    assert not db.exists()
    ref = CodeRef("api", "m.py", 3, "declare_column", "app.orders", "status", "status", "r", 1.0, "status = Column()")
    use = CodeRef("api", "s.py", 9, "use", "app.orders", "status", "getStatus", "use:symbol", 0.8, "o.getStatus()")
    with CodeStore(db) as st:
        st.replace_file("api", "m.py", "s1", "py", [3], [ref], [Unresolved("api", "m.py", 1, "declare_table", "old", "r")])
        st.add_refs([use])
        st.finish_repo("api", "api", "T", "fp")
    with CodeStore(db, readonly=True) as st:
        got = st.where("app.orders", "status")
        assert [(r["kind"], r["path"], r["line"]) for r in got] == [("declare_column", "m.py", 3), ("use", "s.py", 9)]
        assert st.counts("app.orders")["use"] == 1
        assert st.files_matching("m.py") == [("api", "m.py")]
        assert st.unresolved() == [{"repo": "api", "path": "m.py", "line": 1, "kind": "declare_table", "name": "old", "rule": "r"}]
        assert st.stats()["repos"][0]["refs_by_kind"]["declare_column"] == 1
    with CodeStore(db) as st:
        st.delete_file("api", "m.py")
        assert [r["kind"] for r in st.where("app.orders")] == ["use"] and st.file_hashes("api") == {}
