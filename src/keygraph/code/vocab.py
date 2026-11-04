"""The vocabulary the code index matches against: table and column names of a built keygraph, naming variants,
and the common-name set."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..model import is_copy_schema

COMMON = frozenset({"id", "name", "type", "status", "title", "code", "value", "key", "data", "created_at", "updated_at",
                    "deleted_at", "description", "state", "date", "amount", "price", "count"})
_QUOTES = "`\"[]'"


def snake(name: str) -> str:
    """customerId -> customer_id, HTTPCode -> http_code, Customer-Id -> customer_id."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name.replace("-", "_"))
    return re.sub(r"_+", "_", s).lower()


def camel(name: str) -> str:
    """customer_id -> customerId."""
    parts = [p for p in snake(name).split("_") if p]
    return parts[0] + "".join(p.title() for p in parts[1:]) if parts else name


def pascal(name: str) -> str:
    """customer_id -> CustomerId."""
    return "".join(p.title() for p in snake(name).split("_") if p) or name


def variants(name: str) -> list[str]:
    """The identifiers code uses for one property or column: itself, snake, camel, Pascal and its accessors."""
    p = pascal(name)
    out = [name, snake(name), camel(name), p, f"get{p}", f"set{p}", f"is{p}", f"has{p}"]
    return list(dict.fromkeys(out))


class Vocab:
    """Tables and columns of a built graph. Mirror and copy-schema tables are only used when no other table matches."""

    def __init__(self, tables: dict[str, list[str]], mirrors: set[str] | frozenset[str] = frozenset()):
        self.tables = {t: list(cols) for t, cols in sorted(tables.items())}
        self.mirrors = set(mirrors)
        self._ids = {t.lower(): t for t in self.tables}
        self._bare: dict[str, list[str]] = {}
        self._cols: dict[str, dict[str, str]] = {}
        self._by_col: dict[str, list[str]] = {}
        for t, cols in self.tables.items():
            self._bare.setdefault(t.rsplit(".", 1)[-1].lower(), []).append(t)
            low = {c.lower(): c for c in cols}
            self._cols[t] = low
            for c in low:
                self._by_col.setdefault(c, []).append(t)

    @classmethod
    def from_graph(cls, data: dict, copy_schemas=()) -> "Vocab":
        """Copy schemas (declared globs stored in the graph, plus `copy_schemas`) are treated like mirrors."""
        globs = list(data.get("copy_schemas") or []) + list(copy_schemas or [])
        tables = {n["id"]: [c["name"] for c in n["columns"]] for n in data["nodes"]}
        return cls(tables, {n["id"] for n in data["nodes"] if n.get("mirror_of") or is_copy_schema(n["schema"], globs)})

    @classmethod
    def load(cls, db_path: str | Path, copy_schemas=()) -> "Vocab":
        """The vocabulary of the graph.json that sits next to keygraph.db."""
        g = Path(db_path).parent / "graph.json"
        if not g.exists():
            raise ValueError(f"no graph at {g}; build the graph (keygraph load or scan) before indexing code")
        return cls.from_graph(json.loads(g.read_text(encoding="utf-8")), copy_schemas)

    def _prefer(self, ids: list[str]) -> list[str]:
        real = [t for t in ids if t not in self.mirrors]
        return sorted(real or ids)

    def resolve_table(self, name: str) -> list[str]:
        """Table ids a name in code may mean: exact schema.table, else every table with that bare name (mirror
        tables only when nothing else matches). Quotes and brackets are ignored; case is ignored."""
        n = "".join(ch for ch in str(name) if ch not in _QUOTES).strip().lower()
        if not n:
            return []
        if n in self._ids:
            return [self._ids[n]]
        return self._prefer(self._bare.get(n.rsplit(".", 1)[-1], []))

    def column(self, table_id: str, name: str) -> str | None:
        """The real column of a table for a column or property name: exact, snake_case, or snake_case + _id."""
        cols = self._cols.get(table_id, {})
        for cand in (name, snake(name), snake(name) + "_id"):
            if cand.lower() in cols:
                return cols[cand.lower()]
        return None

    def tables_with_column(self, name: str) -> list[str]:
        return self._prefer(self._by_col.get(name.lower(), []))

    def fingerprint(self) -> str:
        return json.dumps([self.tables, sorted(self.mirrors)], sort_keys=True)
