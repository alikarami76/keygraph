"""MongoDB: schema inferred from sampled documents (nested paths become columns), indexes from the collection's
index information, `_id` as primary key, DBRef values as declared references. The database object is injected
(pymongo style) and wrapped so only read operations can be called."""

from __future__ import annotations

from ..model import Column, ForeignKey, Index, Schema, Table
from .base import Source

READ_DB = {"list_collection_names", "__getitem__", "name"}
READ_COLL = {"find", "index_information", "estimated_document_count", "name"}


class ReadOnlyProxy:
    def __init__(self, obj, allowed):
        self._o, self._a = obj, allowed

    def __getattr__(self, k):
        if k not in self._a:
            raise PermissionError(f"keygraph is read-only: '{k}' is not allowed")
        return getattr(self._o, k)

    def __getitem__(self, k):
        if "__getitem__" not in self._a:
            raise PermissionError("not allowed")
        return ReadOnlyProxy(self._o[k], READ_COLL)


def _tname(v) -> str:
    n = type(v).__name__
    return {"str": "string", "int": "int", "float": "double", "bool": "bool", "NoneType": "null", "datetime": "date", "dict": "object",
            "list": "array", "Int64": "long", "Decimal128": "decimal"}.get(n, n)


def flatten(doc: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in doc.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and not ("$ref" in v and "$id" in v) and v:
            out.update(flatten(v, path + "."))
        else:
            out[path] = v
    return out


class MongoSource(Source):
    kind, dialect = "mongodb", "mongo"
    read_only = "only list_collection_names, find, index_information and estimated_document_count are reachable"

    def __init__(self, db, schema_name: str = "default", sample: int = 200):
        self.db, self.schema_name, self.n = ReadOnlyProxy(db, READ_DB), schema_name, sample

    def schema(self) -> Schema:
        sch = Schema()
        for name in sorted(self.db.list_collection_names()):
            coll = self.db[name]
            t = Table(self.schema_name, name)
            types: dict[str, dict] = {}
            refs: dict[str, str] = {}
            docs = list(coll.find({}).limit(self.n))
            for d in docs:
                for path, v in flatten(d).items():
                    types.setdefault(path, {}).setdefault(_tname(v), 0)
                    types[path][_tname(v)] += 1
                    if isinstance(v, dict) and "$ref" in v and "$id" in v:
                        refs[path] = v["$ref"]
            for i, (path, tc) in enumerate(sorted(types.items(), key=lambda x: (x[0] != "_id", x[0])), 1):
                main = max(tc, key=tc.get)
                present = sum(tc.values())
                t.columns.append(Column(path, main if len(tc) == 1 else "/".join(sorted(tc)), present < len(docs), i,
                                        key="PRI" if path == "_id" else "", comment="inferred from sampled documents"))
            if "_id" in types:
                t.indexes.append(Index("_id_", ["_id"], True, True))
            try:
                for iname, spec in coll.index_information().items():
                    cols = [k for k, _ in spec.get("key", [])]
                    if iname != "_id_" and cols:
                        t.indexes.append(Index(iname, cols, bool(spec.get("unique"))))
            except Exception:  # noqa: BLE001
                pass
            for path, ref in refs.items():
                t.foreign_keys.append(ForeignKey(f"dbref_{path}", [path], self.schema_name, ref, ["_id"]))
            try:
                t.info["rows_estimate"] = coll.estimated_document_count()
            except Exception:  # noqa: BLE001
                pass
            sch.add(t)
        return sch

    def sample(self, table_id: str, cols: list[str], n: int, strategy: str = "limit", pk=None):
        name = table_id.split(".", 1)[1]
        docs = [flatten(d) for d in self.db[name].find({}).limit(n)]
        return cols, [tuple(d.get(c) for c in cols) for d in docs]
