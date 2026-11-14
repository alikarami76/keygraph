import json
import re
import shutil
from pathlib import Path

import pytest
from test_example_shop import _build

from keygraph.cli import describe_result
from keygraph.context import ContextDB
from keygraph.index import replay as RP
from keygraph.index import store as ST

V_OK = {"kind": "relations", "index": 0, "verified": True, "check": "relation_overlap", "rate": 0.97, "n": 200}


def _answers(out: Path) -> None:
    ST.save_answer(out, {"unit_id": "aaa", "tables": ["shop.orders"]}, "job-0001", {
        "relations": [{"from_table": "shop.returns", "from_columns": ["order_id"], "to_table": "shop.orders_shadow", "to_columns": ["id"],
                       "reason": "same ids", "confidence": 0.9},
                      {"from_table": "shop.orders", "from_columns": ["address_id"], "to_table": "shop.customer_addresses",
                       "to_columns": ["id"], "reason": "name and values", "confidence": 0.7}],
        "columns": [{"table": "shop.orders", "column": "total", "meaning": "ignored", "null_meaning": "never null", "reason": "r",
                     "confidence": 0.8},
                    {"table": "shop.carts", "column": "customer_id", "meaning": "Cart owner", "codes": {"1": "x"}, "reason": "r", "confidence": 0.7}],
        "tables": [{"table": "shop.carts", "purpose": "A basket", "default_filters": ["status = 1"], "reason": "r", "confidence": 0.7}],
        "performance": [{"table": "shop.carts", "hints": ["filter customer_id"], "reason": "r", "confidence": 0.6}]},
        "claude-x", "h1", [V_OK])
    ST.save_answer(out, {"unit_id": "bbb", "tables": ["shop.returns"]}, "job-0001", {
        "relations": [{"from_table": "shop.carts", "from_columns": ["id"], "to_table": "shop.orders_shadow", "to_columns": ["id"],
                       "reason": "wrong guess", "confidence": 0.95}],
        "concepts": [{"name": "basket", "definition": "Goods before checkout", "synonyms": ["cart", "سبد"], "tables": ["shop.carts"],
                      "reason": "r", "confidence": 0.7}],
        "metrics": [{"name": "open carts", "definition": "Carts not checked out", "expression": "COUNT(*)", "tables": ["shop.carts"],
                     "reason": "r", "confidence": 0.5}]},
        "claude-x", "h2", [{"kind": "relations", "index": 0, "verified": False, "check": "relation_overlap", "rate": 0.0, "n": 50}])


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("replayshop")
    _build(out)
    RP.snapshot_base(out)
    _answers(out)
    return out


@pytest.fixture()
def out(built, tmp_path):
    d = tmp_path / "out"
    shutil.copytree(built, d)
    return d


def _outputs(d: Path) -> dict:
    files = ["graph.json", "RELATIONS_REPORT.md", "keygraph.html", *sorted(f"{x.parent.name}/{x.name}" for x in [*d.glob("tables/*.md"), *d.glob("areas/*.md")])]
    return {f: (d / f).read_bytes() for f in files}


def test_snapshot_base_copies_the_deterministic_graph(built):
    assert (built / "index" / "base_graph.json").read_bytes() == (built / "graph.json").read_bytes()


def test_replay_twice_is_byte_identical(out):
    a = RP.replay(out)
    first = _outputs(out)
    b = RP.replay(out)
    assert a == b and _outputs(out) == first
    assert len(first) > 10
    g = json.loads(first["graph.json"])
    llm = [e for e in g["edges"] if e.get("source") == "llm"]
    assert [e["id"] for e in llm] == ["shop.returns(order_id)->shop.orders_shadow(id)"]
    assert llm[0]["confidence"] == 0.9 and "llm: same ids" in llm[0]["evidence"]
    assert not any(e["id"].startswith("shop.carts(id)->") for e in g["edges"])  # failed probe stays out
    assert a["units"] == 2 and a["llm_edges"] == 1 and a["relations"] == {"rejected": 1, "unverified": 1, "verified": 1}


def test_replay_matches_across_a_fresh_directory(built, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    shutil.copytree(built, a)
    shutil.copytree(built, b)
    RP.replay(a)
    RP.replay(a)  # a different history: an extra pass
    RP.replay(b)
    assert _outputs(a) == _outputs(b)


def test_finish_then_replay_is_byte_identical(out):
    fin = RP.finish(out)
    first = _outputs(out)
    RP.replay(out)
    assert _outputs(out) == first
    s = out / "index" / "summary.json"
    text = s.read_text()
    assert json.loads(text) == fin and not re.search(r"\d{4}-\d{2}-\d{2}", text) and list(json.loads(text)) == sorted(json.loads(text))
    assert fin["facts"]["relation"] == 3 and fin["verified_facts"] == 1


def test_replay_needs_no_environment_and_no_connection(out, monkeypatch):
    for k in list(__import__("os").environ):
        if k.endswith(("URL", "CMD")) or k.startswith("KEYGRAPH"):
            monkeypatch.delenv(k)
    import socket

    def no_net(*a, **k):
        raise AssertionError("replay opened a socket")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    assert RP.replay(out)["units"] == 2


def test_replay_removes_what_a_deleted_answer_wrote(out):
    RP.replay(out)
    (out / "index" / "answers" / "aaa.json").unlink()
    res = RP.replay(out)
    g = json.loads((out / "graph.json").read_text())
    assert not any(e.get("source") == "llm" for e in g["edges"]) and res["llm_edges"] == 0
    with ContextDB(out / "keygraph.db") as ctx:
        assert ctx.db.execute("SELECT null_meaning FROM column_semantics WHERE table_id='shop.orders' AND col='total'").fetchone()[0] == ""
        assert ctx.db.execute("SELECT COUNT(*) FROM column_semantics WHERE source='llm'").fetchone()[0] == 0
        assert ctx.db.execute("SELECT COUNT(*) FROM index_relations").fetchone()[0] == 1


def test_a_human_edit_after_a_fill_survives_replay(out):
    RP.replay(out)
    with ContextDB(out / "keygraph.db") as ctx:
        ctx.db.execute("UPDATE column_semantics SET null_meaning='human words' WHERE table_id='shop.orders' AND col='total'")
    RP.replay(out)
    with ContextDB(out / "keygraph.db") as ctx:
        assert ctx.db.execute("SELECT null_meaning FROM column_semantics WHERE table_id='shop.orders' AND col='total'").fetchone()[0] == "human words"


def test_rebuild_keeps_the_llm_edges(out):
    RP.replay(out)
    before = json.loads((out / "graph.json").read_text())
    _build(out)  # the same inputs again, as `keygraph load` would
    after = json.loads((out / "graph.json").read_text())
    assert [e for e in after["edges"] if e.get("source") == "llm"] == [e for e in before["edges"] if e.get("source") == "llm"]
    assert after["edges"] == before["edges"]
    with ContextDB(out / "keygraph.db") as ctx:
        assert ctx.db.execute("SELECT meaning FROM column_semantics WHERE table_id='shop.carts'").fetchone()[0] == "Cart owner"


def test_render_of_a_saved_graph_does_not_double_apply(out):
    from keygraph.cli import main
    RP.replay(out)
    edges = json.loads((out / "graph.json").read_text())["edges"]
    assert main(["render", "--dir", str(out)]) == 0
    assert json.loads((out / "graph.json").read_text())["edges"] == edges
    again = (out / "graph.json").read_bytes()
    assert main(["render", "--dir", str(out)]) == 0
    assert (out / "graph.json").read_bytes() == again


def test_snapshot_base_drops_llm_edges(out, built):
    RP.replay(out)
    base = out / "index" / "base_graph.json"
    base.unlink()
    RP.snapshot_base(out)
    assert '"llm"' not in base.read_text()
    assert json.loads(base.read_text())["edges"] == json.loads((built / "index" / "base_graph.json").read_text())["edges"]


def test_describe_after_replay_shows_the_labels(out):
    RP.replay(out)
    from keygraph.graph import Graph
    g = Graph.load(out / "graph.json")
    d = describe_result(out, g, "shop.carts", min_confidence=0)
    assert d["provenance"]["columns"]["customer_id"] == "llm"
    assert d["performance"]["hints"] == ["filter customer_id"] and d["performance"]["default_filters"] == ["status = 1"]


def test_replay_without_a_base_graph_says_what_to_run(out):
    (out / "index" / "base_graph.json").unlink()
    with pytest.raises(FileNotFoundError, match="index start"):
        RP.replay(out)


def test_replay_applies_answers_in_unit_id_order(out):
    shutil.rmtree(out / "index" / "answers")
    fill = {"table": "shop.orders", "column": "total", "meaning": "m", "reason": "r", "confidence": 0.5}
    own = {"table": "shop.carts", "column": "customer_id", "reason": "r", "confidence": 0.5}
    for uid in ("b1", "a1"):  # written in the opposite order of their ids
        ST.save_answer(out, {"unit_id": uid, "tables": []}, "job-1", {"columns": [
            {**fill, "null_meaning": f"from {uid}"}, {**own, "meaning": f"from {uid}"}]}, "m", "h", [])
    RP.replay(out)
    with ContextDB(out / "keygraph.db") as ctx:
        q = lambda sql: ctx.db.execute(sql).fetchone()[0]
        assert q("SELECT null_meaning FROM column_semantics WHERE table_id='shop.orders' AND col='total'") == "from a1"  # first fill wins
        assert q("SELECT meaning FROM column_semantics WHERE table_id='shop.carts' AND col='customer_id'") == "from b1"  # last llm row wins
        assert q("SELECT unit_id FROM fact_provenance WHERE kind='column' AND ref='shop.carts.customer_id'") == "b1"


def test_replay_does_not_resurrect_a_rejected_edge(tmp_path):
    from test_index_store import build_with_overrides
    out = tmp_path / "ov"
    build_with_overrides(out)
    RP.snapshot_base(out)
    ST.save_answer(out, {"unit_id": "aaa", "tables": []}, "job-1", {"relations": [
        {"from_table": "billing.invoices", "from_columns": ["customer_id"], "to_table": "shop.customers", "to_columns": ["id"],
         "reason": "r", "confidence": 0.9},
        {"from_table": "shop.returns", "from_columns": ["order_id"], "to_table": "shop.orders_shadow", "to_columns": ["id"],
         "reason": "r", "confidence": 0.9}]}, "m", "h", [])
    res = RP.replay(out)
    ids = [e["id"] for e in json.loads((out / "graph.json").read_text())["edges"]]
    assert "billing.invoices(customer_id)->shop.customers(id)" not in ids
    assert "shop.returns(order_id)->shop.orders_shadow(id)" in ids and res["llm_edges"] == 1
    _build_again = RP.replay(out)  # and a second replay keeps it out
    assert _build_again == res
