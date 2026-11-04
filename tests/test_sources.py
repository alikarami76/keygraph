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
