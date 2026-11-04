"""Rewrite the golden files after an intended change: uv run python tests/regen_golden.py (then review the diff)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `tests` imports when run as a script

from keygraph.code import sql as SQL
from keygraph.code.vocab import Vocab
from tests.codekit import GOLDEN, RULE_REPOS, build_graph_dir, dumps_golden, rules_snapshot, sql_snapshot

if __name__ == "__main__":
    vocab = Vocab.load(build_graph_dir(Path(tempfile.mkdtemp())))
    GOLDEN.mkdir(exist_ok=True)
    for repo in RULE_REPOS:
        (GOLDEN / f"{repo}.json").write_text(dumps_golden(rules_snapshot(repo, vocab)), encoding="utf-8")
    mode = "tokens" if SQL.sqlglot is None else "sqlglot"  # run once with and once without --extra code
    (GOLDEN / f"raw-sql.{mode}.json").write_text(dumps_golden(sql_snapshot("raw-sql", vocab)), encoding="utf-8")
    print("wrote", sorted(p.name for p in GOLDEN.iterdir()))
