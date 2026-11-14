"""The agent-facing docs must name only `keygraph index` subcommands and `index_*` MCP tools that exist."""

import re
from pathlib import Path

import pytest

from keygraph.cli import main
from keygraph.doctor import MCP_TOOLS

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "AGENTS.md", "llms.txt", "skills/keygraph/SKILL.md", "setup/SETUP.md"]
LOOP = ("start", "next", "submit", "status", "finish")


def _help_exits_with(*argv: str) -> int:
    with pytest.raises(SystemExit) as e:
        main([*argv, "--help"])
    return e.value.code


def _named(text: str) -> set[str]:
    return set(re.findall(r"keygraph index ([a-z]+)\b", text))


def test_the_index_subcommands_and_a_bogus_one_are_told_apart():
    assert _help_exits_with("index", "start") == 0
    assert _help_exits_with("index", "nonesuch") == 2


@pytest.mark.parametrize("doc", DOCS)
def test_every_index_subcommand_a_doc_names_exists(doc):
    text = (ROOT / doc).read_text()
    named = _named(text)
    assert set(LOOP) <= named | set(re.findall(r"`index ([a-z]+)", text)), f"{doc} must show the index loop"
    for sub in sorted(named):
        assert _help_exits_with("index", sub) == 0, f"{doc} names `keygraph index {sub}`, which is not in the CLI"


@pytest.mark.parametrize("doc", DOCS)
def test_every_index_tool_a_doc_names_exists(doc):
    text = (ROOT / doc).read_text()
    # `index_information` is a MongoDB method named in the README, so only the loop verbs count as tool names.
    named = set(re.findall(r"\bindex_(start|next|submit|status|finish|replay|refresh)\b", text))
    assert {"start", "next", "submit", "status", "finish"} <= named, f"{doc} must name the five MCP tools"
    for verb in sorted(named):
        assert f"index_{verb}" in MCP_TOOLS, f"{doc} names MCP tool index_{verb}, which does not exist"
