import importlib.util
import sqlite3
from pathlib import Path

import pytest

from keygraph.adapters.dump import load_dump
from keygraph.graph import build_graph
from keygraph.infer import Options

FIX = Path(__file__).parent / "fixtures"

# Optional extras: keygraph has zero core dependencies, so tests that need one skip when it is missing.
needs_yaml = pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="needs PyYAML (extra: yaml)")
needs_sqlalchemy = pytest.mark.skipif(importlib.util.find_spec("sqlalchemy") is None, reason="needs SQLAlchemy (extra: live)")


@pytest.fixture(scope="session")
def dump_schema():
    return load_dump(FIX / "shop_columns.json", FIX / "shop_statistics.json", FIX / "shop_key_columns.json")


@pytest.fixture(scope="session")
def graph(dump_schema):
    return build_graph(dump_schema, options=Options(prefixes=["tbl_"]))


def edge(graph, frm, cols, to):
    for e in graph.edges:
        if e["from"] == frm and e["from_columns"] == cols and e["to"] == to:
            return e
    return None


@pytest.fixture()
def sqlite_url(tmp_path):
    path = tmp_path / "shop.db"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE products(id INTEGER PRIMARY KEY, title TEXT);
    CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), created_by_id INTEGER);
    CREATE INDEX ix_orders_customer ON orders(customer_id);
    CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER);
    INSERT INTO customers VALUES (1,'a'),(2,'b'),(3,'c');
    INSERT INTO products VALUES (1,'p1'),(2,'p2');
    INSERT INTO orders VALUES (1,1,NULL),(2,2,NULL),(3,3,NULL);
    INSERT INTO order_items VALUES (1,1,1),(2,1,2),(3,2,1),(4,3,99),(5,99,2);
    """)
    con.commit()
    con.close()
    return f"sqlite:///{path}"
