"""Second pass of the code index: where declared properties and columns are used.

Symbols come from the repo's declarations (property or column name plus naming variants and accessors). A
symbol whose base name is common (id, status, ...) or that five or more tables declare counts only in files that
name the entity class or the table; a symbol two to four tables declare gives one ref per table with the
confidence divided between them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .refs import CONF_USE_ELSEWHERE, CONF_USE_NAMED, CodeRef, snippet
from .vocab import COMMON, variants
from .walk import Lines

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
GUARD_TABLES = 5


@dataclass
class SymbolIndex:
    # symbol -> sorted [(table_id, column, base name)]
    symbols: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    # table_id -> names that identify it in a file: entity class names and the bare table name
    names: dict[str, set[str]] = field(default_factory=dict)
    # table_id -> its declared or referenced columns (for string literals)
    columns: dict[str, set[str]] = field(default_factory=dict)


def build_index(decls: list[dict]) -> SymbolIndex:
    """decls: declare_table and declare_column rows ({"table", "column", "symbol", "kind"}) of one repo."""
    idx = SymbolIndex()
    sym: dict[str, set[tuple[str, str, str]]] = {}
    for d in decls:
        t = d["table"]
        idx.names.setdefault(t, {t.rsplit(".", 1)[-1]})
        if d["kind"] == "declare_table":
            if d.get("symbol"):
                idx.names[t].add(d["symbol"])
            continue
        col = d["column"]
        idx.columns.setdefault(t, set()).add(col)
        base = d.get("symbol") or col
        for s in variants(base) + variants(col):
            sym.setdefault(s, set()).add((t, col, base))
    idx.symbols = {s: sorted(v) for s, v in sorted(sym.items())}
    return idx


def _is_guarded(cands: list[tuple[str, str, str]]) -> bool:
    tables = {t for t, _, _ in cands}
    return len(tables) >= GUARD_TABLES or any(b.lower() in COMMON or c.lower() in COMMON for _, c, b in cands)


def find_uses(repo: str, path: str, text: str, idx: SymbolIndex, skip_lines: set[int],
              file_tables: set[str], lines: Lines | None = None) -> list[CodeRef]:
    """Use refs in one file. skip_lines: the file's declaration lines; file_tables: tables the file declares or
    references in SQL (string literals equal to one of their columns count as uses)."""
    L = lines or Lines(text)
    words = set(IDENT.findall(text))
    named = {t for t, ns in idx.names.items() if ns & words}
    out: dict[tuple, CodeRef] = {}
    for m in IDENT.finditer(text):
        s = m.group(0)
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        quoted = before in "'\"`" and after == before and before != ""
        line = L.line(m.start())
        if line in skip_lines:
            continue
        if quoted:
            hits = [(t, s, CONF_USE_NAMED) for t in sorted(file_tables) if s in idx.columns.get(t, ())]
            rule = "use:literal"
        else:
            cands = idx.symbols.get(s)
            if not cands:
                continue
            here = [c for c in cands if c[0] in named]
            if here:
                hits = [(t, c, CONF_USE_NAMED / len(here)) for t, c, _ in here]
            elif _is_guarded(cands):
                continue
            else:
                hits = [(t, c, CONF_USE_ELSEWHERE / len(cands)) for t, c, _ in cands]
            rule = "use:symbol"
        for t, c, conf in hits:
            k = (line, t, c)
            if k not in out:
                out[k] = CodeRef(repo, path, line, "use", t, c, s, rule, round(conf, 4), snippet(L.get(line)))
    return sorted(out.values(), key=CodeRef.sort_key)
