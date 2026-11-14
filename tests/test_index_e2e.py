"""A scripted fake agent indexes the shop demo end to end through the CLI: a rejected answer that is resubmitted, a true
relation and a planted false one checked against a copy of shop.db, finish, then a replay that matches byte for byte."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_example_shop import SHOP, _build

from keygraph.cli import main

ORDERS_UNIT = "949700962b66"
MARKER = "MARKER_ZEBRA_7731"
TRUE_REL = {"from_table": "billing.payments", "from_columns": ["order_id"], "to_table": "shop.orders", "to_columns": ["id"],
            "cardinality": "many-to-one", "reason": f"payments point at the order they pay {MARKER}", "confidence": 0.9}
FALSE_REL = {"from_table": "shop.orders", "from_columns": ["total"], "to_table": "shop.customers", "to_columns": ["id"],
             "cardinality": "many-to-one", "reason": f"both are numbers {MARKER}", "confidence": 0.8}
REJECTED_FIRST = {"relations": [{**TRUE_REL, "to_table": "shop.not_a_table"}]}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("index_e2e")
    _build(out)
    return out


def _answer(unit: str, tasks: list[str], attempt: int) -> dict:
    ans: dict = {k: [] for k in tasks}
    if unit == ORDERS_UNIT:
        ans["relations"] = [TRUE_REL, FALSE_REL] if attempt > 1 else REJECTED_FIRST["relations"]
        if attempt > 1:
            ans["columns"] = [{"table": "shop.orders", "column": "currency", "meaning": f"Currency of the total {MARKER}",
                               "codes": {"EUR": "euro"}, "reason": MARKER, "confidence": 0.8}]
            ans["tables"] = [{"table": "shop.sellers", "purpose": f"Marketplace sellers {MARKER}", "reason": MARKER, "confidence": 0.7}]
            ans["concepts"] = [{"name": "checkout", "definition": "A customer buys", "synonyms": ["purchase"],
                                "tables": ["shop.orders"], "reason": MARKER, "confidence": 0.7}]
    return ans


def _tree(out: Path) -> dict[str, bytes]:
    files = [out / "graph.json", out / "RELATIONS_REPORT.md", out / "keygraph.html", *sorted((out / "areas").glob("*.md")),
             *sorted((out / "tables").glob("*.md"))]
    return {str(f.relative_to(out)): f.read_bytes() for f in files if f.exists()}


def test_fake_agent_indexes_the_shop_end_to_end(built, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    shutil.copytree(built, out)
    probe_db = tmp_path / "probe.db"
    shutil.copyfile(SHOP / "shop.db", probe_db)
    monkeypatch.setenv("SHOP_PROBE_URL", f"sqlite:///{probe_db}")
    monkeypatch.setenv("KEYGRAPH_DEV", "1")
    con = sqlite3.connect(SHOP / "shop.db")
    raw_values = [con.execute("SELECT email FROM customers LIMIT 1").fetchone()[0],
                  con.execute("SELECT gateway_ref FROM payments LIMIT 1").fetchone()[0]]
    con.close()

    def cli(*argv, expect=0):
        capsys.readouterr()
        assert main([*argv, "--dir", str(out), "--json"]) == expect
        return json.loads(capsys.readouterr().out)

    def submit(unit, answer):
        f = tmp_path / "answer.json"
        f.write_text(json.dumps(answer), encoding="utf-8")
        return cli("index", "submit", unit, str(f), "--model", "fake-agent-1")

    base_graph = json.loads((out / "graph.json").read_text())
    started = cli("index", "start", "--url-env", "SHOP_PROBE_URL", "--dialect", "sqlite")
    assert started["units"] == 18 and started["url_env"] == "SHOP_PROBE_URL"
    assert "sqlite:" not in json.dumps(started)  # only the name is stored
    results = {}
    while True:
        n = cli("index", "next")
        if n["status"] != "unit":
            break
        res = submit(n["unit_id"], _answer(n["unit_id"], n["tasks"], n["attempt"]))
        results.setdefault(n["unit_id"], []).append(res)
    assert n == {"status": "done", "finish": "keygraph index finish"}

    first, second = results[ORDERS_UNIT]
    assert first["status"] == "rejected" and first["attempts_left"] == 2 and first["accepted"] == 0
    assert second["status"] == "accepted" and second["rejected"] == [] and second["attempts_left"] == 0
    assert second["probe_source"] == "url"
    assert second["verified"] >= 2 and second["probe_failed"] == 1  # the true relation and the codes passed; the planted one did not
    assert all(r[0]["status"] == "accepted" for u, r in results.items() if u != ORDERS_UNIT)
    st = cli("index", "status")
    assert st["units"] == {"pending": 0, "done": 18, "failed": 0, "skipped": 0} and st["accept_rate"] is not None

    fin = cli("index", "finish")
    assert fin["units"] == 18 and fin["llm_edges"] >= 0 and fin["relations"] == {"rejected": 1, "verified": 1}
    after = _tree(out)
    graph = json.loads(after["graph.json"])
    ev = [e for e in graph["edges"] if e["from"] == "billing.payments" and e["from_columns"] == ["order_id"] and e["to"] == "shop.orders"]
    assert ev and any(x.startswith("llm: ") for x in ev[0]["evidence"])
    assert not [e for e in graph["edges"] if e["from"] == "shop.orders" and e["from_columns"] == ["total"]]  # the false one is not in the map
    declared = lambda g: [e for e in g["edges"] if e["kind"] == "declared"]  # noqa: E731
    assert declared(graph) == declared(base_graph)  # declared foreign keys are untouched
    assert (out / "index" / "answers" / f"{ORDERS_UNIT}.json").exists()

    replayed = cli("index", "replay")
    assert replayed == fin and _tree(out) == after  # byte-identical graph.json, pages, report and explorer

    # the dev log holds ids, sizes, counts and check names, and no values, reasons or answer text
    log = "".join(p.read_text(encoding="utf-8") for p in (out / "dev").glob("events*.jsonl"))
    assert '"tool": "index submit"' in log and "index_submit" in log and "relation_overlap" in log
    assert MARKER not in log and "not a table in the map" not in log and "probe.db" not in log
    assert all(v not in log for v in raw_values)
    for line in log.splitlines():
        ev = json.loads(line)
        if ev["tool"].startswith("index "):
            assert "answer" not in ev.get("args", {}) and MARKER not in json.dumps(ev)
