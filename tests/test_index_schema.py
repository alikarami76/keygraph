import copy

import pytest

from keygraph.context import ContextDB
from keygraph.index import jobs as J
from keygraph.index import schema as S


def _answer(kinds=S.TASKS):
    return {"model": "test-model", **S.examples(kinds)}


def test_examples_validate_for_every_kind():
    assert S.validate_answer(_answer()) == []
    for k in S.TASKS:
        assert S.validate_answer(_answer([k]), [k]) == []


def test_missing_kind_and_unknown_kind():
    a = _answer(["relations"])
    assert S.validate_answer(a, ["relations", "columns"]) == ["$: missing columns"]
    with pytest.raises(ValueError, match="unknown task"):
        S.answer_schema(["relations", "nope"])
    assert S.validate_answer({**a, "extra": 1}, ["relations"]) == ["$: unknown field extra"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda i: i.pop("reason"), "missing reason"),
    (lambda i: i.pop("confidence"), "missing confidence"),
    (lambda i: i.update(confidence=1.5), "between 0 and 1"),
    (lambda i: i.update(confidence="high"), "expected number"),
    (lambda i: i.update(confidence=True), "expected number"),
    (lambda i: i.update(reason=""), "must not be empty"),
    (lambda i: i.update(reason="x" * 241), "longer than 240"),
    (lambda i: i.update(from_columns="order_id"), "expected array"),
    (lambda i: i.update(from_table=5), "expected string"),
    (lambda i: i.update(cardinality="some"), "must be one of"),
    (lambda i: i.update(bogus=1), "unknown field bogus"),
    (lambda i: i.pop("to_columns"), "missing to_columns"),
])
def test_malformed_relation_item_fails(mutate, needle):
    a = copy.deepcopy(_answer(["relations"]))
    mutate(a["relations"][0])
    errs = S.validate_answer(a, ["relations"])
    assert errs and any(needle in e for e in errs) and all(e.startswith("$.relations[0]") for e in errs)


def test_not_an_object_and_wrong_list():
    assert S.validate_answer([], ["relations"]) == ["$: expected object"]
    assert S.validate_answer({"relations": {}}, ["relations"]) == ["$.relations: expected array"]


def test_codes_object_checked():
    a = _answer(["columns"])
    a["columns"][0]["codes"] = {"1": 5}
    assert any("codes.1: expected string" in e for e in S.validate_answer(a, ["columns"]))


def test_every_kind_requires_reason_and_confidence():
    for k, item in S.ITEMS.items():
        assert {"reason", "confidence"} <= set(item["required"]), k


def test_prompt_hash_is_stable_and_covers_all_parts():
    sch, ins, pk = S.answer_schema(["relations"]), S.instructions(["relations"]), {"b": 1, "a": [1, 2]}
    h = S.prompt_hash(ins, sch, pk)
    assert h == S.prompt_hash(ins, sch, {"a": [1, 2], "b": 1}) and len(h) == 64
    assert h != S.prompt_hash(ins + " ", sch, pk)
    assert h != S.prompt_hash(ins, S.answer_schema(["columns"]), pk)
    assert h != S.prompt_hash(ins, sch, {"b": 1, "a": [1, 3]})


def test_schema_and_instructions_are_deterministic_and_versioned():
    assert S.canonical(S.answer_schema()) == S.canonical(S.answer_schema(list(reversed(S.TASKS))))
    assert S.answer_schema()["schema_version"] == S.SCHEMA_VERSION == 1
    assert all(k in S.instructions(["metrics"]) for k in ("metrics", "reason", "confidence"))
    assert "relations:" not in S.instructions(["metrics"])


def test_job_default_schema_version(tmp_path):
    with ContextDB(tmp_path / "k.db") as ctx:
        jid = J.create_job(ctx, [], budget=1)
        assert J.get_job(ctx, jid)["schema_version"] == str(S.SCHEMA_VERSION)
