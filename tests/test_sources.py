import pytest

from keygraph.adapters import open_source
from keygraph.adapters.clickhouse import ClickHouseSource
from keygraph.adapters.metabase import MetabaseSource
from keygraph.adapters.mongo import MongoSource
from keygraph.adapters.mysql import MySQLSource
from keygraph.adapters.postgres import PostgresSource
from keygraph.adapters.sqlserver import SQLServerSource
from keygraph.graph import build_graph
from keygraph.model import parse_enum_members, type_family
from keygraph.probe import UnsafeSQL


def test_enum_parsing():
    assert parse_enum_members("enum('new','paid','it''s')") == ["new", "paid", "it's"]
    assert parse_enum_members("Nullable(Enum8('a' = 1, 'b' = 2))") == ["a=1", "b=2"]
    assert parse_enum_members("varchar(10)") == []
    assert type_family("LowCardinality(Nullable(String))") == "string" and type_family("Enum8('a' = 1)") == "string"


def fake_mysql(sql):
    s = sql.lower()
    if "information_schema.columns" in s:
        return [{"TABLE_SCHEMA": "d", "TABLE_NAME": "orders", "COLUMN_NAME": "id", "ORDINAL_POSITION": 1, "COLUMN_TYPE": "bigint unsigned",
                 "DATA_TYPE": "bigint", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI", "COLUMN_DEFAULT": None, "EXTRA": "auto_increment",
                 "CHARACTER_SET_NAME": None, "COLLATION_NAME": None, "COLUMN_COMMENT": "row id", "GENERATION_EXPRESSION": ""},
                {"TABLE_SCHEMA": "d", "TABLE_NAME": "orders", "COLUMN_NAME": "status", "ORDINAL_POSITION": 2, "COLUMN_TYPE": "enum('new','paid')",
                 "DATA_TYPE": "enum", "IS_NULLABLE": "YES", "COLUMN_KEY": "", "COLUMN_DEFAULT": "new", "EXTRA": "", "CHARACTER_SET_NAME": "utf8mb4",
                 "COLLATION_NAME": "utf8mb4_general_ci", "COLUMN_COMMENT": "", "GENERATION_EXPRESSION": ""}]
    if "information_schema.tables" in s:
        return [{"TABLE_SCHEMA": "d", "TABLE_NAME": "orders", "ENGINE": "InnoDB", "TABLE_ROWS": 1234, "DATA_LENGTH": 16384, "INDEX_LENGTH": 0,
                 "TABLE_COMMENT": "orders", "CREATE_OPTIONS": "partitioned"}]
    if "check_constraints" in s:
        return [{"TABLE_SCHEMA": "d", "TABLE_NAME": "orders", "CONSTRAINT_NAME": "ck", "CHECK_CLAUSE": "id > 0"}]
    if "statistics" in s:
        return [{"TABLE_SCHEMA": "d", "TABLE_NAME": "orders", "INDEX_NAME": "PRIMARY", "NON_UNIQUE": 0, "SEQ_IN_INDEX": 1, "COLUMN_NAME": "id"}]
    return []


def test_mysql_source_details_and_guard():
    src = MySQLSource(fake_mysql)
    sch = src.schema()
    t = sch.tables["d.orders"]
    assert t.primary_key == ["id"] and t.info["engine"] == "InnoDB" and t.info["rows_estimate"] == 1234
    assert t.info["checks"][0]["expr"] == "id > 0"
    st = t.column("status")
    assert st.values == ["new", "paid"] and st.default == "new" and st.collation == "utf8mb4_general_ci"
    assert t.column("id").extra == "auto_increment" and t.column("id").comment == "row id"
    with pytest.raises(UnsafeSQL):
        src.run("DELETE FROM x")
    g = build_graph(sch)
    from keygraph.query import column_detail
    assert "members: new, paid" in column_detail(g.nodes["d.orders"]["columns"][1])


def test_clickhouse_source():
    def run(sql):
        if "system.tables" in sql:
            return [("db", "events", "MergeTree", 100, 5000, "toYYYYMM(day)", "user_id, day", "user_id", "web events")]
        if "system.columns" in sql:
            return [("db", "events", "user_id", 1, "UInt64", "", "", ""), ("db", "events", "day", 2, "Date", "", "", ""),
                    ("db", "events", "kind", 3, "LowCardinality(Enum8('view' = 1, 'click' = 2))", "", "", "event kind")]
        if "data_skipping" in sql:
            return [("db", "events", "ix_kind", "set", "kind", 1)]
        return []
    sch = ClickHouseSource(run, ["db"]).schema()
    t = sch.tables["db.events"]
    assert t.primary_key == ["user_id"] and [i.columns for i in t.indexes if i.name == "ORDER BY"] == [["user_id", "day"]]
    assert t.info["partition_key"] == "toYYYYMM(day)" and t.column("kind").values == ["view=1", "click=2"]
    assert any(i.name.startswith("ix_kind") for i in t.indexes)


def test_postgres_and_sqlserver_sources():
    def pg(sql):
        if "information_schema.columns" in sql:
            return [("public", "orders", "id", 1, "integer", "NO", "nextval('s')", "NEVER", None, "pk"),
                    ("public", "orders", "state", 2, "order_state", "YES", None, "NEVER", None, None)]
        if "pg_enum" in sql:
            return [("order_state", "new,paid")]
        if "pg_index" in sql:
            return [("public", "orders", "orders_pkey", True, True, "id")]
        if "pg_constraint" in sql and "'f'" in sql:
            return []
        if "pg_class c" in sql and "reltuples" in sql:
            return [("public", "orders", 10, 8192, "orders table")]
        return []
    t = PostgresSource(pg).schema().tables["public.orders"]
    assert t.primary_key == ["id"] and t.column("state").values == ["new", "paid"] and t.info["rows_estimate"] == 10
    assert t.column("id").default == "nextval('s')"

    def ms(sql):
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return [("dbo", "t", "a", 1, "int", "NO", None, None, None), ("dbo", "t", "b", 2, "nvarchar(50)", "YES", None, "Latin1", "iso_1")]
        if "sys.indexes" in sql:
            return [("dbo", "t", "pk_t", True, True, "a", 1)]
        if "sys.foreign_key_columns" in sql:
            return [("dbo", "t", "fk", "b", "dbo", "u", "id", 1)]
        if "sys.partitions" in sql:
            return [("dbo", "t", 42)]
        return []
    t = SQLServerSource(ms).schema().tables["dbo.t"]
    assert t.primary_key == ["a"] and t.foreign_keys[0].ref_table == "u" and t.info["rows_estimate"] == 42
    assert "TOP 5" in SQLServerSource(ms).table_ref("dbo.t") + " TOP 5"
    with pytest.raises(UnsafeSQL):
        SQLServerSource(ms).run("EXEC xp_cmdshell 'dir'")


def test_metabase_source_and_fingerprints():
    calls = []

    def http(method, path, body=None):
        calls.append((method, path))
        if path.startswith("/api/database/7/metadata"):
            return {"tables": [
                {"id": 1, "name": "users", "schema": "public", "fields": [
                    {"id": 10, "name": "id", "database_type": "int4", "semantic_type": "type/PK", "database_position": 0,
                     "fingerprint": {"global": {"distinct-count": 5, "nil%": 0.0}, "type": {"type/Number": {"min": 1, "max": 5, "q1": 2, "q3": 4, "avg": 3}}}}]},
                {"id": 2, "name": "orders", "schema": "public", "fields": [
                    {"id": 20, "name": "user_ref", "database_type": "int4", "semantic_type": "type/FK", "fk_target_field_id": 10, "database_position": 0}]}]}
        if path == "/api/dataset":
            return {"data": {"cols": [{"name": "n"}], "rows": [[1]]}}
    src = MetabaseSource(http, 7)
    sch = src.schema()
    assert sch.tables["public.orders"].foreign_keys[0].ref_table == "users" and sch.tables["public.users"].primary_key == ["id"]
    assert src.fingerprints()["public.users.id"]["numeric"]["max"] == 5
    assert src.run("SELECT 1") == [{"n": 1}]
    with pytest.raises(UnsafeSQL):
        src.run("UPDATE users SET a=1")
    assert all(m == "GET" or p == "/api/dataset" for m, p in calls)


class FakeCursor(list):
    def limit(self, n):
        return FakeCursor(self[:n])


class FakeColl:
    def __init__(self, docs):
        self.docs = docs

    def find(self, q=None):
        return FakeCursor(self.docs)

    def index_information(self):
        return {"_id_": {"key": [("_id", 1)]}, "by_owner": {"key": [("owner_id", 1)], "unique": False}}

    def estimated_document_count(self):
        return len(self.docs)

    def insert_one(self, d):  # must be unreachable
        raise AssertionError("write reached")


class FakeDb:
    def __init__(self):
        self.c = {"users": FakeColl([{"_id": 1, "name": "a"}, {"_id": 2, "name": "b", "profile": {"age": 3}}]),
                  "posts": FakeColl([{"_id": 9, "owner_id": 1, "author": {"$ref": "users", "$id": 1}}])}

    def list_collection_names(self):
        return list(self.c)

    def __getitem__(self, k):
        return self.c[k]


def test_mongo_source_infers_and_is_read_only():
    src = MongoSource(FakeDb())
    sch = src.schema()
    u, p = sch.tables["default.users"], sch.tables["default.posts"]
    assert u.primary_key == ["_id"] and u.column("profile.age").type == "int" and u.column("name").nullable is False
    assert any(i.name == "by_owner" for i in p.indexes) and p.foreign_keys[0].ref_table == "users"
    g = build_graph(sch)
    assert any(e["from"] == "default.posts" and e["to"] == "default.users" for e in g.edges)
    with pytest.raises(PermissionError):
        src.db["users"].insert_one({})
    with pytest.raises(PermissionError):
        src.db.drop_collection("users")
    cols, rows = src.sample("default.users", ["_id", "profile.age"], 10)
    assert rows == [(1, None), (2, 3)]


def test_open_source_registry():
    assert isinstance(open_source("clickhouse", run=lambda s: []), ClickHouseSource)
    with pytest.raises(ValueError):
        open_source("oracle")


class _FakeRunner:
    def __init__(self, run):
        self.run = run


def _patch_runner(monkeypatch, run, seen=None):
    from keygraph.probe import SelectOnlyRunner

    def from_url(url, as_dicts=False):
        if seen is not None:
            seen.append(as_dicts)
        return _FakeRunner(run)
    monkeypatch.setattr(SelectOnlyRunner, "from_url", staticmethod(from_url))


def _no_inspector(monkeypatch):
    from keygraph.adapters import live

    def boom(*a, **k):
        raise AssertionError("the inspector scan ran")
    monkeypatch.setattr(live, "scan_url", boom)


def test_scan_mysql_url_uses_dedicated_adapter(monkeypatch, tmp_path):
    import json

    from keygraph.cli import main
    _patch_runner(monkeypatch, fake_mysql, seen := [])
    _no_inspector(monkeypatch)
    out = tmp_path / "o"
    assert main(["scan", "--url", "mysql+pymysql://u:pw@db.internal:3306/d", "--dir", str(out)]) == 0
    assert seen == [True]
    g = json.loads((out / "graph.json").read_text())
    cols = {c["name"]: c for c in g["nodes"][0]["columns"]}
    assert cols["status"]["values"] == ["new", "paid"] and cols["id"]["comment"] == "row id"
    assert g["nodes"][0]["info"]["rows_estimate"] == 1234
    b = json.loads((out / "build.json").read_text())
    assert b["sources"] == [{"kind": "mysql", "database": "d"}]
    assert "pw" not in (out / "build.json").read_text() and "db.internal" not in (out / "build.json").read_text()


def test_scan_sqlite_stays_on_inspector(tmp_path, monkeypatch):
    import json
    import sqlite3

    from keygraph.cli import main
    pytest.importorskip("sqlalchemy")
    db = tmp_path / "s.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)")
    c.commit()
    c.close()
    _patch_runner(monkeypatch, lambda sql: (_ for _ in ()).throw(AssertionError("dedicated adapter used")))
    out = tmp_path / "o"
    assert main(["scan", "--url", f"sqlite:///{db}", "--dir", str(out)]) == 0
    assert json.loads((out / "build.json").read_text())["sources"][0]["kind"] == "sqlalchemy"


def test_scan_falls_back_when_adapter_fails(monkeypatch, tmp_path, capsys):
    import json

    from keygraph.adapters import live
    from keygraph.cli import main
    from keygraph.model import Column, Schema, Table

    def broken(sql):
        raise RuntimeError("secret-host refused")
    _patch_runner(monkeypatch, broken)
    sch = Schema()
    sch.add(Table("d", "t", [Column("id", "int")]))
    monkeypatch.setattr(live, "scan_url", lambda *a, **k: sch)
    out = tmp_path / "o"
    assert main(["scan", "--url", "mysql://u:pw@secret-host/d", "--dir", str(out)]) == 0
    err = capsys.readouterr().err
    assert "mysql adapter failed (RuntimeError" in err and "secret-host" not in err and "pw" not in err
    assert json.loads((out / "build.json").read_text())["sources"][0]["kind"] == "sqlalchemy"


def test_scan_generic_flag_forces_inspector(monkeypatch, tmp_path):
    from keygraph.adapters import live
    from keygraph.cli import main
    from keygraph.model import Column, Schema, Table
    _patch_runner(monkeypatch, lambda sql: (_ for _ in ()).throw(AssertionError("dedicated adapter used")))
    sch = Schema()
    sch.add(Table("d", "t", [Column("id", "int")]))
    monkeypatch.setattr(live, "scan_url", lambda *a, **k: sch)
    assert main(["scan", "--url", "mysql://u@h/d", "--generic", "--dir", str(tmp_path / "o")]) == 0


def test_scan_postgres_keeps_requested_schemas_only(monkeypatch):
    from keygraph.adapters import live
    from keygraph.model import Column, Schema, Table
    full = Schema()
    for s in ("public", "audit"):
        full.add(Table(s, "t", [Column("id", "int")]))

    class Src:
        def schema(self):
            return full
    monkeypatch.setattr("keygraph.adapters.open_source", lambda kind, **k: Src())
    _patch_runner(monkeypatch, lambda sql: [])
    assert list(live.dedicated_scan("postgres", "postgresql://h/d", None, False).tables) == ["public.t"]
    assert list(live.dedicated_scan("postgres", "postgresql://h/d", ["audit"], False).tables) == ["audit.t"]
    assert sorted(live.dedicated_scan("postgres", "postgresql://h/d", None, True).tables) == ["audit.t", "public.t"]


def test_row_dict_keeps_duplicate_labels():
    from keygraph.probe import row_dict
    assert row_dict(["a", "b", "a", "a"], (1, 2, 3, 4)) == {"a": 1, "b": 2, "a_2": 3, "a_3": 4}


def test_runner_keeps_duplicate_labels_from_real_sqlalchemy(tmp_path):
    pytest.importorskip("sqlalchemy")
    from keygraph.probe import SelectOnlyRunner
    r = SelectOnlyRunner.from_url(f"sqlite:///{tmp_path / 'x.db'}", as_dicts=True)
    assert list(r.run("SELECT 1 AS relname, 2 AS relname")[0].values()) == [1, 2]


def test_postgres_adapter_survives_duplicate_labels():
    """The real Postgres query shapes repeat relname/nspname/array_to_string; a dict-per-row runner must not collapse them."""
    from keygraph.adapters import postgres as P
    from keygraph.probe import row_dict

    labels = {P.IDX: ["nspname", "relname", "relname", "indisunique", "indisprimary", "array_to_string"],
              P.FK: ["nspname", "relname", "conname", "array_to_string", "nspname", "relname", "array_to_string"],
              P.COLS: ["table_schema", "table_name", "column_name", "ordinal_position", "case", "is_nullable", "column_default",
                       "is_generated", "generation_expression", "col_description"]}
    data = {P.COLS: [("app", "orders", "id", 1, "bigint", "NO", None, "NEVER", None, None),
                     ("app", "users", "id", 1, "bigint", "NO", None, "NEVER", None, None)],
            P.IDX: [("app", "users", "users_pkey", True, True, "id"), ("app", "orders", "orders_pkey", True, True, "id")],
            P.FK: [("app", "orders", "fk_u", "user_id", "app", "users", "id")]}

    def run(sql):
        for q, rows in data.items():
            if sql == q:
                return [row_dict(labels[q], r) for r in rows]
        return []
    sch = P.PostgresSource(run).schema()
    assert sch.tables["app.users"].primary_key == ["id"]
    fk = sch.tables["app.orders"].foreign_keys[0]
    assert (fk.columns, fk.ref_table, fk.ref_columns) == (["user_id"], "users", ["id"])


def test_postgres_default_schema_is_read_from_the_database(monkeypatch):
    from keygraph.adapters import live
    from keygraph.model import Column, Schema, Table
    full = Schema()
    for s in ("tenant_a", "public"):
        full.add(Table(s, "t", [Column("id", "int")]))
    seen = []

    class Src:
        def schema(self):
            return full
    monkeypatch.setattr("keygraph.adapters.open_source", lambda kind, **k: Src())
    _patch_runner(monkeypatch, lambda sql: seen.append(sql) or [{"current_schema": "tenant_a"}])
    assert list(live.dedicated_scan("postgres", "postgresql://h/d", None, False).tables) == ["tenant_a.t"]
    assert seen == ["SELECT current_schema()"]


def test_empty_scan_fails_clearly_without_fallback(monkeypatch, tmp_path, capsys):
    from keygraph.adapters import live
    from keygraph.cli import main
    from keygraph.model import Schema

    class Src:
        def schema(self):
            return Schema()
    monkeypatch.setattr("keygraph.adapters.open_source", lambda kind, **k: Src())
    _patch_runner(monkeypatch, lambda sql: [{"x": "public"}])
    _no_inspector(monkeypatch)
    assert main(["scan", "--url", "postgres://u:pw@secret-host/d", "--dir", str(tmp_path / "o")]) == 2
    err = capsys.readouterr().err
    assert "no tables in schema public" in err and "--all-schemas" in err and "secret-host" not in err and "pw" not in err
    assert live.url_dialect("postgres://h/d") == "postgres"


def test_postgres_scheme_is_normalised_for_both_paths(monkeypatch):
    from keygraph.adapters import live
    from keygraph.model import Schema
    seen = []
    monkeypatch.setattr(live, "dedicated_scan", lambda kind, url, s, a: seen.append(url) or Schema())
    live.scan_any("postgres://h/d")
    assert seen == ["postgresql://h/d"]


def test_fallback_warning_redacts_url_pieces(monkeypatch, tmp_path, capsys):
    from keygraph.adapters import live
    from keygraph.model import Column, Schema, Table

    def broken(sql):
        raise RuntimeError("could not connect to mysql://bob:hunter2@db.internal:3306/shop: host db.internal user bob password hunter2")
    _patch_runner(monkeypatch, broken)
    sch = Schema()
    sch.add(Table("d", "t", [Column("id", "int")]))
    monkeypatch.setattr(live, "scan_url", lambda *a, **k: sch)
    live.scan_any("mysql://bob:hunter2@db.internal:3306/shop")
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "could not connect" in err
    for frag in ("hunter2", "db.internal", "bob", "3306"):
        assert frag not in err


def test_mysql_scan_drops_foreign_keys_to_other_schemas(monkeypatch):
    from keygraph.adapters import live
    from keygraph.model import Column, ForeignKey, Schema, Table
    s = Schema()
    s.add(Table("d", "t", [Column("id", "int")], foreign_keys=[ForeignKey("f", ["id"], "other", "x", ["id"])]))

    class Src:
        def schema(self):
            return s
    monkeypatch.setattr("keygraph.adapters.open_source", lambda kind, **k: Src())
    _patch_runner(monkeypatch, lambda sql: [])
    assert live.dedicated_scan("mysql", "mysql://h/d", None, False).tables["d.t"].foreign_keys == []
