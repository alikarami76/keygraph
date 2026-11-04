"""Related-schema preference and declared copy schemas (by glob) in inference and the code vocabulary."""
import json

from keygraph.cli import main, run_pipeline
from keygraph.code.vocab import Vocab
from keygraph.graph import build_graph
from keygraph.infer import Options, related_schemas
from keygraph.model import Column, Index, Schema, Table

PK = [Index("PRIMARY", ["id"], True, True)]


def tbl(schema, name, *cols):
    return Table(schema, name, [Column("id", "int", False, 1, "PRI")] + [Column(c, "int", True, i + 2) for i, c in enumerate(cols)], list(PK))


def graph(tables, **opts):
    s = Schema()
    for t in tables:
        s.add(t)
    return build_graph(s, None, Options(**opts))


def edges(g, frm, col):
    return [e for e in g.edges if e["from"] == frm and e["from_columns"] == [col]]


ORDERS = tbl("app", "orders", "user_id")
USERS = [tbl("ods_app", "users"), tbl("ods_billing", "users"), tbl("crm", "users")]


def test_related_schema_token_rules():
    assert related_schemas("app", "ods_app") and related_schemas("ods_app", "app")
    assert related_schemas("app", "app_v2") and related_schemas("app", "app_replica")
    assert not related_schemas("app", "apple") and not related_schemas("app", "ods_billing")
    assert not related_schemas("app", "app")


def test_single_related_candidate_beats_unrelated_ones():
    es = edges(graph([ORDERS] + USERS), "app.orders", "user_id")
    assert [e["to"] for e in es] == ["ods_app.users"]
    assert not es[0]["ambiguous"]
    assert "target is in related schema 'ods_app'" in es[0]["evidence"]


def test_reverse_direction_related_schema():
    es = edges(graph([tbl("ods_app", "orders", "user_id"), tbl("app", "users"), tbl("crm", "users")]), "ods_app.orders", "user_id")
    assert [e["to"] for e in es] == ["app.users"]


def test_unrelated_cross_schema_candidates_stay_ambiguous():
    es = edges(graph([ORDERS, tbl("crm", "users"), tbl("billing", "users")]), "app.orders", "user_id")
    assert len(es) == 2 and all(e["ambiguous"] for e in es)
    assert not any("related schema" in x for e in es for x in e["evidence"])


def test_same_schema_candidate_still_wins():
    es = edges(graph([ORDERS, tbl("app", "users")] + USERS), "app.orders", "user_id")
    assert [e["to"] for e in es] == ["app.users"]
    assert not any("related schema" in x for x in es[0]["evidence"])


def test_declared_copy_loses_to_non_copy_target():
    tables = [tbl("shop", "orders", "user_id"), tbl("ch_cache", "users"), tbl("crm", "users")]
    plain = edges(graph(tables), "shop.orders", "user_id")
    assert len(plain) == 2 and all(e["ambiguous"] for e in plain)
    es = edges(graph(tables, copy_schemas=["ch_*"]), "shop.orders", "user_id")
    best = max(es, key=lambda e: e["confidence"])
    assert best["to"] == "crm.users" and not best["ambiguous"]
    cp = next(e for e in es if e["to"] == "ch_cache.users")
    assert "target schema is a declared copy" in cp["evidence"] and cp["confidence"] < best["confidence"]


def test_copy_penalty_needs_a_non_copy_rival():
    es = edges(graph([tbl("shop", "orders", "user_id"), tbl("ch_cache", "users")], copy_schemas=["ch_*"]), "shop.orders", "user_id")
    assert len(es) == 1 and "target schema is a declared copy" not in es[0]["evidence"]


def test_vocab_prefers_non_copy_tables():
    data = {"copy_schemas": ["ods_*"], "nodes": [
        {"id": "ods_app.users", "schema": "ods_app", "columns": [{"name": "id"}]},
        {"id": "crm.users", "schema": "crm", "columns": [{"name": "id"}]},
        {"id": "ods_app.logs", "schema": "ods_app", "columns": [{"name": "id"}]}]}
    v = Vocab.from_graph(data)
    assert v.resolve_table("users") == ["crm.users"]
    assert v.resolve_table("logs") == ["ods_app.logs"]  # the only match, so the copy is used
    assert Vocab.from_graph({**data, "copy_schemas": []}, ["crm"]).resolve_table("users") == ["ods_app.users"]


def test_copies_in_context_yaml_reach_inference_graph_and_vocab(tmp_path):
    ctx = tmp_path / "ctx.yaml"
    ctx.write_text('copies: ["ch_*"]\nmirrors: {legacy: false}\n')
    out = tmp_path / "out"
    sch = Schema()
    for t in [tbl("shop", "orders", "user_id"), tbl("ch_cache", "users"), tbl("crm", "users")]:
        sch.add(t)
    run_pipeline(sch, out, None, [str(ctx)], verbose=False)
    g = json.loads((out / "graph.json").read_text())
    assert g["copy_schemas"] == ["ch_*"]
    assert not any(n.get("mirror_of") for n in g["nodes"])  # not hidden as mirrors
    e = [x for x in g["edges"] if x["from"] == "shop.orders"]
    assert any("target schema is a declared copy" in x["evidence"] for x in e)
    assert Vocab.load(out / "keygraph.db").resolve_table("users") == ["crm.users"]


def test_copies_inside_mirrors_mapping(tmp_path):
    from keygraph.context import read_copy_globs
    f = tmp_path / "m.yaml"
    f.write_text('mirrors:\n  copies: ["ods_*", "ch_*"]\n  reporting: shop\n')
    assert read_copy_globs([str(f)]) == ["ods_*", "ch_*"]


def test_code_index_copy_schema_flag_is_remembered(tmp_path):
    out = tmp_path / "out"
    sch = Schema()
    for t in [tbl("ods_app", "users"), tbl("crm", "users")]:
        sch.add(t)
    run_pipeline(sch, out, verbose=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.sql").write_text("SELECT id FROM users;\n")
    assert Vocab.load(out / "keygraph.db").resolve_table("users") == ["crm.users", "ods_app.users"]
    assert main(["code", "index", str(repo), "--dir", str(out), "--copy-schema", "ods_*"]) == 0
    assert json.loads((out / "graph.json").read_text())["copy_schemas"] == ["ods_*"]
    assert Vocab.load(out / "keygraph.db").resolve_table("users") == ["crm.users"]
