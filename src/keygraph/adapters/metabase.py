"""Metabase REST API: metadata (tables, fields, semantic types, foreign-key targets Metabase knows), free profile
statistics from field fingerprints, and native read-only queries.

`http(method, path, body=None) -> parsed JSON` is injected, so authentication (session token or API key) stays
outside this module. Read-only: only GET requests and POST /api/dataset with a native SELECT (assert_select)."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..model import Column, ForeignKey, Index, Schema
from .base import Source, guarded


class MetabaseSource(Source):
    kind, dialect, quote = "metabase", "generic", '"'

    def __init__(self, http: Callable, database_id: int, schema_name: str | None = None):
        self.http, self.db_id, self.schema_name = http, database_id, schema_name
        self._fp: dict = {}
        self.run = guarded(self._native)

    def _native(self, sql: str) -> list:
        res = self.http("POST", "/api/dataset", {"database": self.db_id, "type": "native", "native": {"query": sql}})
        data = res.get("data", res)
        names = [c["name"] for c in data.get("cols", [])]
        return [dict(zip(names, row)) for row in data.get("rows", [])]

    def schema(self) -> Schema:
        meta = self.http("GET", f"/api/database/{self.db_id}/metadata?include_hidden=false")
        sch = Schema()
        fields_by_id: dict = {}
        for tb in meta.get("tables", []):
            sub = tb.get("schema") if tb.get("schema") not in (None, "", "public", "default") else ""
            s = (f"{self.schema_name}_{sub}" if sub else self.schema_name) if self.schema_name else (tb.get("schema") or "default")
            t = sch.get_or_create(s, tb["name"])
            if tb.get("description"):
                t.info["comment"] = tb["description"]
            for f in sorted(tb.get("fields", []), key=lambda f: f.get("database_position", 0)):
                sem = f.get("semantic_type") or ""
                c = Column(f["name"], f.get("database_type") or f.get("base_type", ""), not f.get("database_required", False),
                           f.get("database_position", 0) + 1, key="PRI" if sem == "type/PK" else "",
                           comment=(f.get("description") or "") + (f" [semantic: {sem}]" if sem and sem != "type/PK" else ""))
                t.columns.append(c)
                fields_by_id[f["id"]] = (t, c)
                if f.get("fingerprint"):
                    self._fp[f"{t.id}.{c.name}"] = (f["fingerprint"], c.type)
            pk = [c.name for c in t.columns if c.key == "PRI"]
            if pk:
                t.indexes.append(Index("PRIMARY", pk, True, True))
        for tb in meta.get("tables", []):
            for f in tb.get("fields", []):
                tgt = fields_by_id.get(f.get("fk_target_field_id"))
                src = fields_by_id.get(f["id"])
                if tgt and src:
                    src[0].foreign_keys.append(ForeignKey(f"metabase_fk_{f['name']}", [f["name"]], tgt[0].schema, tgt[0].name, [tgt[1].name]))
        return sch

    def fingerprints(self) -> dict:
        """Profiles derived from Metabase field fingerprints (approximate, no query needed)."""
        out = {}
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for key, (fp, _ty) in self._fp.items():
            g = fp.get("global", {})
            p = {"method": "metabase fingerprint (sampled by Metabase)", "profiled_at": now, "sample_size": None,
                 "null_rate": g.get("nil%"), "distinct_in_sample": g.get("distinct-count")}
            ty = fp.get("type", {})
            if "type/Number" in ty:
                n = ty["type/Number"]
                p["numeric"] = {"min": n.get("min"), "max": n.get("max"), "p25": n.get("q1"), "p75": n.get("q3"), "mean": n.get("avg")}
            if "type/Text" in ty:
                p["length"] = {"avg": ty["type/Text"].get("average-length")}
            if "type/DateTime" in ty:
                d = ty["type/DateTime"]
                p["dates"] = {"min": d.get("earliest"), "max": d.get("latest"), "recency_days": None}
            out[key] = p
        return out
