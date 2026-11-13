"""The FK recovery bench script: stripped copies, hit detection, recall and precision math."""
import json
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.conftest import needs_sqlalchemy

SCRIPT = Path(__file__).resolve().parent.parent / "bench" / "scripts" / "fk_recovery.py"


def _make(path: Path, ddl: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    for s in ddl:
        con.execute(s)
    con.commit()
    con.close()


def _run(tmp_path: Path) -> dict:
    root = tmp_path / "dbs"
    _make(root / "shop" / "shop.sqlite", [
        "create table customers (id integer primary key, name text)",
        "create table orders (id integer primary key, customer_id integer references customers(id), total real)",
        "insert into customers values (1, 'a')",
    ])
    _make(root / "odd" / "odd.sqlite", [
        "create table Alpha (k integer primary key, v text)",
        "create table Beta (x integer primary key, zz integer, FOREIGN KEY (zz) REFERENCES Alpha)",
        "create table Pair (a integer, b integer, primary key (a, b))",
        "create table PairUse (p1 integer, p2 integer, foreign key (p1, p2) references Pair)",
    ])
    out = tmp_path / "res.json"
    subprocess.run([sys.executable, "-I", str(SCRIPT), "--db-root", str(root), "--work", str(tmp_path / "work"),
                    "--keygraph", f"{shlex.quote(sys.executable)} -m keygraph", "--out", str(out), "--md", str(tmp_path / "res.md"),
                    "--label", "t"], check=True, capture_output=True, text=True)
    return json.loads(out.read_text())


@needs_sqlalchemy
def test_fk_recovery_scoring(tmp_path):
    res = _run(tmp_path)
    dbs = {r["db"]: r for r in res["databases"]}
    shop, odd = dbs["shop"]["scores"]["0.6"], dbs["odd"]["scores"]["0.6"]
    assert shop["declared"] == 1 and shop["hits"] == 1
    assert shop["misses"] == []
    # Beta.zz -> Alpha resolves to Alpha's PK k; composite FK counts one pair per column
    assert odd["declared"] == 3
    assert "alpha.k <-> beta.zz" in odd["misses"]
    assert odd["hits"] <= 1
    # the copy carries no FKs and no rows
    con = sqlite3.connect(tmp_path / "work" / "shop" / "stripped.sqlite")
    assert con.execute("pragma foreign_key_list(orders)").fetchall() == []
    assert con.execute("select count(*) from customers").fetchone()[0] == 0
    # totals math
    t = res["total"]["0.6"]
    assert t["declared"] == 4 and t["hits"] == shop["hits"] + odd["hits"]
    assert t["recall"] == round(t["hits"] / 4, 4)
    assert t["precision_lower_bound"] == (round(t["hits"] / t["inferred"], 4) if t["inferred"] else None)
    assert res["keygraph_version"] and len(dbs["shop"]["sha256"]) == 64


def test_hit_is_direction_blind_and_case_insensitive():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import fk_recovery as fr
    finally:
        sys.path.pop(0)
    truth = {frozenset({("orders", "customer_id"), ("customers", "id")})}
    edges = [(frozenset({("customers", "id"), ("orders", "customer_id")}), 0.9, "x"),
             (frozenset({("a", "b"), ("c", "d")}), 0.7, "y")]
    s = fr.score(truth, edges, 0.6)
    assert (s["declared"], s["hits"], s["inferred"]) == (1, 1, 2) and s["misses"] == []
    assert [x["pair"] for x in s["extras"]] == ["a.b <-> c.d"]
    s8 = fr.score(truth, edges, 0.8)
    assert (s8["hits"], s8["inferred"]) == (1, 1)
    assert fr.score(truth, [], 0.6)["misses"] == ["customers.id <-> orders.customer_id"]
