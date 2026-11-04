"""Link a query to the tables and columns it touches, through the code index's SQL module."""

from __future__ import annotations

from ..code.sql import analyze
from ..code.vocab import Vocab


def link(sql: str, vocab: Vocab | None, dialect: str | None = None) -> tuple[list[tuple[str, str, str]], list[str]]:
    """([(table_id, column or "", role)], [unresolved table names]). Roles: read, write, filter, join, group; they
    need sqlglot, so without it every ref is read."""
    refs: set[tuple[str, str, str]] = set()
    unresolved: set[str] = set()
    for h in analyze(sql, vocab, dialect):
        ids = vocab.resolve_table(h.table) if vocab else []
        if not ids:
            if h.column is None:
                unresolved.add(h.table)
            continue
        role = h.role if h.via == "sqlglot" else "read"
        for t in ids:
            col = vocab.column(t, h.column) if h.column else ""
            if col is None:
                continue
            refs.add((t, col, role))
    return sorted(refs), sorted(unresolved)
