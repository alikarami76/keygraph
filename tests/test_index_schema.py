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
    assert S.validate_answer({"relations": {}}, ["relations"]) == ["$.relations: expected array (a JSON list; use [] when empty)"]


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


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(bad):
    a = copy.deepcopy(_answer(["relations"]))
    a["relations"][0]["confidence"] = bad
    assert any("finite" in e for e in S.validate_answer(a, ["relations"]))
    import json
    parsed = json.loads('{"relations": [{"from_table": "a.b", "from_columns": ["x"], "to_table": "a.c", "to_columns": ["id"], '
                        '"reason": "r", "confidence": NaN}]}')
    assert S.validate_answer(parsed, ["relations"])


def test_lists_are_capped_at_fifty_items():
    a = _answer(["relations"])
    a["relations"] = [a["relations"][0]] * 51
    assert S.validate_answer(a, ["relations"]) == ["$.relations: more than 50 items"]
    a["relations"] = a["relations"][:50]
    assert S.validate_answer(a, ["relations"]) == []


def test_examples_are_in_the_instructions_and_so_in_the_hash():
    ins = S.instructions(["relations"])
    assert S.canonical(S.EXAMPLES["relations"]) in ins and "schema.table" in ins
    old = S.EXAMPLES["relations"]
    try:
        S.EXAMPLES["relations"] = {**old, "reason": "changed"}
        assert S.prompt_hash(S.instructions(["relations"]), S.answer_schema(["relations"]), {}) != \
            S.prompt_hash(ins, S.answer_schema(["relations"]), {})
    finally:
        S.EXAMPLES["relations"] = old


def test_huge_ints_do_not_overflow_the_number_check():
    errs = S.check(10**400, S.ITEMS["relations"]["properties"]["confidence"])
    assert errs and "between 0 and 1" in errs[0]
    assert S.check(10**400, {"type": "number"}) == [] and S.check(-(10**400), {"type": "number", "minimum": 0})
