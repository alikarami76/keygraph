"""Pattern machinery and the built-in ORM rules, checked against one fixture repo per framework (golden JSON)."""
import json

import pytest

from keygraph.code.patterns import PatternError, builtin_rules, compile_rule, find_blocks, load_patterns, run_rules
from keygraph.code.vocab import Vocab
from tests.codekit import GOLDEN, RULE_REPOS, build_graph_dir, rules_snapshot


@pytest.fixture(scope="module")
def vocab(tmp_path_factory):
    return Vocab.load(build_graph_dir(tmp_path_factory.mktemp("code_rules")))


@pytest.mark.parametrize("repo", RULE_REPOS)
def test_fixture_repo_matches_golden(repo, vocab):
    if repo == "custom-pattern":
        pytest.importorskip("yaml", reason="needs PyYAML (extra: yaml)")
    want = json.loads((GOLDEN / f"{repo}.json").read_text(encoding="utf-8"))
    assert rules_snapshot(repo, vocab) == want


def test_builtin_rules_compile_and_are_unique():
    rules = builtin_rules()
    assert len({r.name for r in rules}) == len(rules) >= 20
    assert all(r.scope == "entity" and r.confidence == 1.0 for r in rules)


def test_blocks_brace_indent_and_nested():
    php = "<?php\nclass A {\n  function f() { return 1; }\n}\nclass B {}\n"
    assert [(b.name, php[b.start:b.end].count("\n")) for b in find_blocks(php, "php")] == [("A", 2), ("B", 0)]
    py = "class Order:\n    x = 1\n\n    class Meta:\n        db_table = 'orders'\n\ny = 2\n"
    blocks = find_blocks(py, "py")
    assert [b.name for b in blocks] == ["Order", "Meta"] and blocks[1].parent == 0
    assert py[blocks[0].start:blocks[0].end].endswith("'orders'\n\n")


def test_entity_unresolved_and_its_columns_dropped(vocab):
    text = "<?php\n#[ORM\\Table(name: 'gone')]\nclass Gone {\n  #[ORM\\Column]\n  private int $status;\n}\n"
    res = run_rules("r", "Gone.php", "php", text, builtin_rules(), vocab)
    assert res.refs == []
    assert [(u.line, u.kind, u.name) for u in res.unresolved] == [(2, "declare_table", "gone")]
    assert res.decl_lines == [2, 4, 5]


def test_file_scope_column_falls_back_to_the_one_table_with_it(vocab):
    rule = compile_rule({"name": "field", "kind": "use", "regex": r"field\('(?P<column>\w+)'\)"})
    res = run_rules("r", "x.php", "php", "<?php\nfield('carrier');\nfield('status');\n", [rule], vocab)
    assert [(r.table_id, r.column, r.confidence) for r in res.refs] == [("app.shipments", "carrier", 0.9)]
    assert [(u.line, u.name) for u in res.unresolved] == [(3, "status")]  # four tables have status


@pytest.mark.parametrize("bad, msg", [
    ({"kind": "use", "regex": "x(?P<column>y)"}, "missing name"),
    ({"name": "p1", "kind": "nope", "regex": "x"}, "pattern p1: kind must be"),
    ({"name": "p2", "kind": "use", "regex": "(?P<column>[a-"}, "pattern p2: invalid regex"),
    ({"name": "p3", "kind": "declare_table", "regex": "(?P<column>x)"}, "pattern p3: a declare_table regex needs a named group table"),
    ({"name": "p4", "kind": "use", "regex": "(?P<col>x)"}, "pattern p4: a use regex needs"),
    ({"name": "p5", "kind": "use", "languages": ["cobol"], "regex": "(?P<column>x)"}, "pattern p5: unknown language"),
    ({"name": "p6", "kind": "use", "confidence": 2, "regex": "(?P<column>x)"}, "pattern p6: confidence"),
])
def test_invalid_patterns_fail_with_the_pattern_name(bad, msg):
    with pytest.raises(PatternError, match=msg.replace("(", r"\(")):
        compile_rule(bad)


def test_pattern_files(tmp_path):
    pytest.importorskip("yaml")
    good = tmp_path / "p.yaml"
    good.write_text("patterns:\n  - name: f\n    kind: use\n    regex: \"get\\\\('(?P<column>\\\\w+)'\\\\)\"\n")
    assert [r.name for r in load_patterns([good])] == ["f"]
    (tmp_path / "j.json").write_text(json.dumps({"patterns": [{"name": "f", "kind": "use", "regex": "(?P<column>x)"}]}))
    with pytest.raises(PatternError, match="duplicate pattern name"):
        load_patterns([good, tmp_path / "j.json"])
    (tmp_path / "bad.yaml").write_text("patterns: [\n")
    with pytest.raises(PatternError, match="bad.yaml: not valid YAML"):
        load_patterns([tmp_path / "bad.yaml"])
    (tmp_path / "flat.yaml").write_text("- name: x\n")
    with pytest.raises(PatternError, match="top-level 'patterns:' list"):
        load_patterns([tmp_path / "flat.yaml"])
