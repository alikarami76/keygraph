"""Graph assembly, persistence and table-name resolution."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from .infer import Inferer, Options, declared_edges, redundant_indexes
from .model import Schema, type_family
from .overrides import Overrides, apply_overrides

VERSION = 1
BUILD_FILE = "build.json"


class AmbiguousTable(ValueError):
    def __init__(self, name: str, candidates: list[str]):
        super().__init__(f"'{name}' is ambiguous; use schema.table: " + ", ".join(candidates))
        self.candidates = candidates


class UnknownTable(ValueError):
    def __init__(self, name: str, suggestions: list[str], coverage: str = ""):
        msg = f"unknown table '{name}'" + (f"; did you mean: {', '.join(suggestions)}" if suggestions else "")
        super().__init__(msg + (f"; {coverage}" if coverage else ""))
        self.suggestions = suggestions


def edge_id(e: dict) -> str:
    return f"{e['from']}({','.join(e['from_columns'])})->{e['to']}({','.join(e['to_columns'])})"


def build_graph(schema: Schema, overrides: Overrides | None = None, options: Options | None = None) -> "Graph":
    opts = options or Options()
    if overrides:
        overrides.configure(opts)
    warnings: list[str] = []
    inf = Inferer(schema, opts)
    declared = declared_edges(schema, warnings)
    inferred = inf.run()
    taken = {(e["from"], tuple(e["from_columns"]), e["to"]) for e in declared}
    edges = declared + [e for e in inferred if (e["from"], tuple(e["from_columns"]), e["to"]) not in taken]
    nodes = []
    for t in sorted(schema.tables.values(), key=lambda x: x.id):
        nodes.append({
            "id": t.id, "schema": t.schema, "table": t.name,
            "info": t.info,
            "columns": [{"name": c.name, "type": c.type, "family": type_family(c.type), "nullable": c.nullable,
                         **({"key": c.key} if c.key else {}),
                         **{k: v for k, v in (("default", c.default), ("extra", c.extra), ("charset", c.charset),
                                              ("collation", c.collation), ("comment", c.comment), ("values", c.values),
                                              ("generated", c.generated)) if v not in (None, "", [])}}
                        for c in sorted(t.columns, key=lambda c: (c.position, c.name))],
            "primary_key": t.primary_key,
            "unique_keys": [{"name": ix.name, "columns": ix.columns, **({"approximate": True} if ix.approximate else {})}
                            for ix in t.indexes if ix.unique and not ix.primary],
            "indexes": [{"name": ix.name, "columns": ix.columns, "unique": ix.unique, "primary": ix.primary,
                         **({"approximate": True} if ix.approximate else {})}
                        for ix in sorted(t.indexes, key=lambda i: (not i.primary, i.name))],
            "polymorphic": inf.polymorphic.get(t.id, []),
            "unresolved_id_columns": sorted(inf.unresolved.get(t.id, [])),
            "redundant_indexes": redundant_indexes(t),
        })
    data = {"version": VERSION, "nodes": nodes, "edges": edges, "warnings": warnings, "overrides": {}}
    if overrides:
        apply_overrides(data, overrides)
    for e in data["edges"]:
        e["id"] = edge_id(e)
    data["edges"].sort(key=lambda e: e["id"])
    data["warnings"] = sorted(set(data["warnings"]))
    g = Graph(data)
    g.refresh_stats()
    return g


class Graph:
    def __init__(self, data: dict):
        self.data = data
        self.nodes: dict[str, dict] = {n["id"]: n for n in data["nodes"]}
        self.edges: list[dict] = data["edges"]
        self._by_name: dict[str, list[str]] = {}
        for nid, n in self.nodes.items():
            self._by_name.setdefault(n["table"].lower(), []).append(nid)
        self._adj: dict[str, list[dict]] | None = None
        self.build: dict | None = None  # build.json next to graph.json; kept out of self.data so graph.json stays byte-stable

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        g = cls(json.loads(Path(path).read_text(encoding="utf-8")))
        bp = Path(path).with_name(BUILD_FILE)
        if bp.exists():
            try:
                b = json.loads(bp.read_text(encoding="utf-8"))
                g.build = b if isinstance(b, dict) else None
            except (OSError, ValueError):
                g.build = None
        return g

    def coverage(self) -> str:
        """Which schemas this map covers and when/from what it was built, for unknown-table answers."""
        schemas = sorted({n["schema"] for n in self.nodes.values()})
        if not schemas:
            return ""
        shown = ", ".join(schemas[:10]) + (f" (+{len(schemas) - 10} more)" if len(schemas) > 10 else "")
        b = self.build or {}
        bits = []
        if b.get("built_at"):
            bits.append("built " + str(b["built_at"])[:10])
        srcs = [f"{x['kind']}:{x['database']}" if x.get("database") else str(x.get("kind", "")) for x in b.get("sources") or []]
        if srcs := [x for x in srcs if x]:
            bits.append("from " + ", ".join(srcs))
        return (f"this map covers schemas {shown}" + (f" ({' '.join(bits)})" if bits else "")
                + "; if the table is in another schema or database, rebuild with --schema/--all-schemas")

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def refresh_stats(self) -> None:
        by_kind: dict[str, int] = {}
        bands = {"1.0": 0, "0.8-0.99": 0, "0.6-0.79": 0, "0.4-0.59": 0, "<0.4": 0}
        for e in self.edges:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
            c = e["confidence"]
            bands["1.0" if c >= 1 else "0.8-0.99" if c >= .8 else "0.6-0.79" if c >= .6 else "0.4-0.59" if c >= .4 else "<0.4"] += 1
        self.data["stats"] = {
            "tables": len(self.nodes), "columns": sum(len(n["columns"]) for n in self.nodes.values()),
            "schemas": len({n["schema"] for n in self.nodes.values()}),
            "indexes": sum(len(n["indexes"]) for n in self.nodes.values()),
            "edges": len(self.edges), "edges_by_kind": by_kind, "edges_by_confidence": bands,
            "polymorphic_columns": sum(len(n["polymorphic"]) for n in self.nodes.values()),
        }
        self._adj = None

    # --- name resolution -------------------------------------------------------
    def resolve(self, name: str) -> str:
        if name in self.nodes:
            return name
        low = name.lower()
        for nid in self.nodes:
            if nid.lower() == low:
                return nid
        if "." not in name:
            hits = self._by_name.get(low, [])
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise AmbiguousTable(name, sorted(hits))
        sug = difflib.get_close_matches(name, list(self.nodes), n=4, cutoff=0.6)
        sug += [h for k in difflib.get_close_matches(low, list(self._by_name), n=3, cutoff=0.7) for h in self._by_name[k]]
        raise UnknownTable(name, sorted(set(sug)), self.coverage())

    def split_column(self, ref: str) -> tuple[str, str | None]:
        """Parse 'table', 'table.col' or 'schema.table.col' into (table_id, column or None)."""
        try:
            return self.resolve(ref), None
        except (UnknownTable, AmbiguousTable) as first:
            if "." not in ref:
                raise
            head, col = ref.rsplit(".", 1)
            tid = self.resolve(head)
            if col not in {c["name"] for c in self.nodes[tid]["columns"]}:
                raise ValueError(f"table {tid} has no column '{col}'") from first
            return tid, col

    # --- adjacency -------------------------------------------------------------
    def adjacency(self) -> dict[str, list[dict]]:
        if self._adj is None:
            adj: dict[str, list[dict]] = {n: [] for n in self.nodes}
            for e in self.edges:
                adj.setdefault(e["from"], []).append(oriented(e, False))
                if e["to"] != e["from"]:
                    adj.setdefault(e["to"], []).append(oriented(e, True))
            self._adj = adj
        return self._adj


def flip_card(c: str) -> str:
    return {"many-to-one": "one-to-many", "one-to-many": "many-to-one"}.get(c, c)


def oriented(e: dict, reverse: bool) -> dict:
    """View an edge from one end: near/far table and columns as walked."""
    if not reverse:
        return {"edge": e, "near": e["from"], "near_cols": e["from_columns"], "far": e["to"],
                "far_cols": e["to_columns"], "cardinality": e["cardinality"], "reverse": False}
    return {"edge": e, "near": e["to"], "near_cols": e["to_columns"], "far": e["from"],
            "far_cols": e["from_columns"], "cardinality": flip_card(e["cardinality"]), "reverse": True}
