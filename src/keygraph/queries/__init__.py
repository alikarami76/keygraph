"""keygraph query log: the SQL people and agents ran, how it performed and the business question it answered,
linked to the tables and columns it touches.

    from keygraph import queries
    queries.add("keygraph-out/keygraph.db", "SELECT ... FROM shop.orders ...", source="warehouse", ms=840,
                context="late orders this week")
    queries.top("keygraph-out/keygraph.db", table="shop.orders")
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import devlog
from ..code.vocab import Vocab
from ..graph import Graph
from .fingerprint import fingerprint, normalize
from .store import QueryStore

__all__ = ["add", "import_jsonl", "top", "slow", "for_ref", "search", "show", "stats", "summary", "fingerprint", "normalize"]
FIELDS = ("sql", "source", "ms", "rows", "context", "tags", "by", "at", "error")


def _vocab(db_path: Path) -> Vocab | None:
    try:
        return Vocab.load(db_path)
    except ValueError:  # no graph yet: runs are stored, tables stay unresolved
        return None


def add(db_path: str | Path, sql: str, source: str, ms: float | None = None, rows: int | None = None, context: str = "",
        tags=(), by: str = "", at: str | None = None, error: str | None = None, keep_literals: bool = False,
        dialect: str | None = None) -> dict:
    db_path = Path(db_path)
    with QueryStore(db_path) as st:
        return st.add(sql, source, ms, rows, context, tags, by, at, error, keep_literals, _vocab(db_path), dialect)


def import_jsonl(db_path: str | Path, path: str | Path, keep_literals: bool = False, dialect: str | None = None) -> dict:
    """One JSON object per line with sql, source, ms, rows, context, tags, by, at, error. Bad lines are reported, not
    stored; good lines are stored even when others fail."""
    db_path = Path(db_path)
    with devlog.phase("queries.import") as ph:
        res = _import_jsonl(db_path, path, keep_literals, dialect)
        ph["added"], ph["errors"] = res["added"], len(res["errors"])  # counts only, never SQL
    return res


def _import_jsonl(db_path: Path, path: str | Path, keep_literals: bool, dialect: str | None) -> dict:
    vocab = _vocab(db_path)
    added, errors = 0, []
    with QueryStore(db_path) as st:
        for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if not isinstance(d, dict):
                    raise ValueError("expected a JSON object")
                unknown = sorted(set(d) - set(FIELDS))
                if unknown:
                    raise ValueError(f"unknown field(s) {', '.join(unknown)}")
                st.add(d.get("sql", ""), d.get("source", ""), d.get("ms"), d.get("rows"), d.get("context", "") or "",
                       d.get("tags") or [], d.get("by", "") or "", d.get("at"), d.get("error"), keep_literals, vocab, dialect)
                added += 1
            except (ValueError, TypeError) as exc:
                errors.append({"line": n, "error": str(exc)})
    return {"added": added, "errors": errors}


def _table(db_path: Path, ref: str | None) -> tuple[str | None, str | None]:
    if not ref:
        return None, None
    g = db_path.parent / "graph.json"
    if not g.exists():
        raise ValueError(f"no graph at {g}; build the graph first")
    return Graph.load(g).split_column(ref)


def top(db_path: str | Path, by: str = "runs", table: str | None = None, limit: int = 10) -> dict:
    db_path = Path(db_path)
    tid, _ = _table(db_path, table)
    with QueryStore(db_path, readonly=True) as st:
        return {"by": by, "table": tid, "queries": st.top(by, tid, limit)}


def slow(db_path: str | Path, limit: int = 10, min_runs: int = 1) -> dict:
    with QueryStore(Path(db_path), readonly=True) as st:
        return {"queries": st.slow(limit, min_runs)}


def for_ref(db_path: str | Path, ref: str) -> dict:
    db_path = Path(db_path)
    tid, col = _table(db_path, ref)
    with QueryStore(db_path, readonly=True) as st:
        return {"table": tid, "column": col, "queries": st.for_ref(tid, col)}


def search(db_path: str | Path, text: str, limit: int = 10) -> dict:
    with QueryStore(Path(db_path), readonly=True) as st:
        return {"query": text, "queries": st.search(text, limit)}


def show(db_path: str | Path, fp: str) -> dict:
    with QueryStore(Path(db_path), readonly=True) as st:
        return st.show(fp)


def stats(db_path: str | Path) -> dict:
    with QueryStore(Path(db_path), readonly=True) as st:
        return st.stats()


def summary(db_path: str | Path, per_table: int = 10) -> dict:
    with QueryStore(Path(db_path), readonly=True) as st:
        return st.summary(per_table)
