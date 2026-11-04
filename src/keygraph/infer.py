"""Relation inference: declared foreign keys plus naming, role, shared-key-name and polymorphic heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Schema, Table, is_copy_schema, type_family, type_length

DEFAULT_ROLE_WORDS = {
    "created", "updated", "deleted", "modified", "approved", "rejected", "assigned", "owner",
    "parent", "original", "old", "new", "prev", "previous", "next", "source", "target", "from",
    "to", "main", "primary", "default", "related", "linked", "buyer", "requested", "last", "first",
}
USER_LIKE = ["user", "users", "account", "accounts", "admin", "admins", "staff", "employee",
             "employees", "member", "members", "person", "people", "operator", "operators"]
BY_ROLES = {"created_by", "updated_by", "deleted_by", "modified_by", "approved_by", "rejected_by",
            "assigned_by", "author", "creator", "owner", "editor", "assignee", "approver",
            "reviewer", "requester", "updater"}
SELF_ROLES = {"parent", "prev", "previous", "next", "original", "root", "master", "superseded"}
POLY_SUFFIXES = ("type", "class", "table", "kind")
LOG_SUFFIXES = ("_logs", "_log", "_history", "_audit")
LOG_COLUMNS = {"entity_id", "record_id", "object_id"}


@dataclass
class Options:
    min_confidence: float = 0.3
    top_k: int = 3
    prefixes: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)  # stem -> table ref
    role_aliases: dict[str, list[str]] = field(default_factory=dict)  # role stem -> table names
    role_words: set[str] = field(default_factory=lambda: set(DEFAULT_ROLE_WORDS))
    auto_prefixes: bool = True
    copy_schemas: list[str] = field(default_factory=list)  # fnmatch globs of schemas that are copies (replicas, BI caches)


def singular(w: str) -> str:
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def plural(w: str) -> str:
    if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
        return w[:-1] + "ies"
    if w.endswith(("s", "x", "z", "ch", "sh")):
        return w + "es"
    return w + "s"


def forms(w: str) -> set[str]:
    s = singular(w)
    return {w, s, plural(s), plural(w) if not w.endswith("s") else w}


def snake(name: str) -> str:
    n = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return n.lower()


def extract_stem(col: str) -> str | None:
    n = snake(col)
    if n == "id":
        return None
    if n.endswith("_id") and len(n) > 3:
        return n[:-3]
    if n.endswith("id") and len(n) > 4 and n[-3].isalpha() and "_" not in n:
        return n[:-2]
    return None


def related_schemas(a: str, b: str) -> bool:
    """Two different schema names are related when one, split on '_', contains the other's whole token run
    (app ~ ods_app, app_replica, app_v2; ods_app ~ app). A partial token never counts (app is not related to apple)."""
    x, y = a.lower().split("_"), b.lower().split("_")
    if x == y:
        return False
    short, long_ = (x, y) if len(x) < len(y) else (y, x)
    n = len(short)
    return any(long_[i:i + n] == short for i in range(len(long_) - n + 1))


def _ref(opts_ref: str, schema: Schema, prefer_schema: str) -> str | None:
    if opts_ref in schema.tables:
        return opts_ref
    hits = [t for t in schema.tables if t.split(".", 1)[1] == opts_ref]
    same = [t for t in hits if t.startswith(prefer_schema + ".")]
    if same:
        return same[0]
    return hits[0] if len(hits) == 1 else None


def log_base(name: str) -> str | None:
    n = name.lower()
    for suf in LOG_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf):
            return n[: -len(suf)]
    return None


def _is_indexed_leading(t: Table, cols: list[str]) -> str | None:
    n = len(cols)
    for ix in t.indexes:
        if len(ix.columns) >= n and set(ix.columns[:n]) == set(cols):
            return ix.name
    return None


def cardinality(t: Table, cols: list[str]) -> str:
    s = set(cols)
    if t.primary_key and set(t.primary_key) == s:
        return "one-to-one"
    if any(set(u) == s for u in t.unique_keys):
        return "one-to-one"
    return "many-to-one"


def target_key(t: Table, stem: str, colname: str):
    pk = t.primary_key
    if len(pk) == 1:
        return pk, "primary key", 0.08
    for u in t.unique_keys:
        if len(u) == 1 and (u[0].lower() in (colname.lower(), "id", stem + "_id")):
            return u, "unique key", 0.03
    if not pk and not t.unique_keys:
        idc = next((c.name for c in t.columns if c.name.lower() == "id"), None)
        if idc:  # no keys declared at all: assume 'id' is the key, scored a little below a real primary key
            return [idc], "assumed key: no keys declared, 'id' column", -0.02
        return None
    for u in ([pk] if pk else []) + t.unique_keys:
        if len(u) > 1 and u[0].lower() == colname.lower() and t.column(u[0]):
            return [u[0]], "leading column of a composite key (partial; join may fan out)", -0.25
    return None


class Inferer:
    def __init__(self, schema: Schema, opts: Options):
        self.s = schema
        self.o = opts
        self.lookup: dict[str, list[tuple[str, str]]] = {}
        self.suffix_lookup: dict[str, list[str]] = {}
        self.pkname: dict[str, list[str]] = {}
        self._index()

    def _auto_prefixes(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        if not self.o.auto_prefixes:
            return out
        for sch in {t.schema for t in self.s.tables.values()}:
            names = [t.name for t in self.s.tables.values() if t.schema == sch]
            cnt: dict[str, int] = {}
            for n in names:
                if "_" in n:
                    cnt[n.split("_", 1)[0]] = cnt.get(n.split("_", 1)[0], 0) + 1
            out[sch] = {p + "_" for p, c in cnt.items() if c >= max(4, int(0.1 * len(names)))}
        return out

    def _index(self) -> None:
        auto = self._auto_prefixes()
        for t in self.s.tables.values():
            name = t.name.lower()
            variants = {name: ""}
            for p in list(self.o.prefixes) + sorted(auto.get(t.schema, ())):
                if name.startswith(p.lower()) and len(name) > len(p):
                    variants.setdefault(name[len(p):], p)
            for v, pref in variants.items():
                for f in forms(v):
                    self.lookup.setdefault(f, []).append((t.id, pref))
            parts = name.split("_")
            for i in range(1, len(parts)):
                suf = "_".join(parts[i:])
                if len(suf) >= 3:
                    for f in forms(suf):
                        self.suffix_lookup.setdefault(f, []).append(t.id)
            pk = t.primary_key
            if len(pk) == 1 and pk[0].lower() != "id":
                self.pkname.setdefault(pk[0].lower(), []).append(t.id)

    def _log_subject(self, t: Table) -> str | None:
        """The table a log/history/audit table is about: '<base>_log' -> '<base>', looked up in the same
        schema, then in the schema with a trailing '_log' removed, then as a single unique match anywhere."""
        base = log_base(t.name)
        sch_base = t.schema[:-4] if t.schema.lower().endswith("_log") and len(t.schema) > 4 else None
        if base is None and sch_base:
            base = t.name.lower()  # a table of a '<app>_log' schema describes the same-named table of '<app>'
        if base is None:
            return None
        fm = forms(base)
        hits = [x for x in self.s.tables.values() if x.id != t.id and x.name.lower() in fm]
        order = [t.schema] + ([sch_base] if sch_base else [])
        if log_base(t.name) is None:
            order = [sch_base]
        for sch in order:
            h = sorted(x.id for x in hits if x.schema == sch)
            if h:
                return h[0]
        if log_base(t.name) is not None and len(hits) == 1:
            return hits[0].id
        return None

    # --- candidate generation -------------------------------------------------
    def _add(self, cands: dict, tid: str, base: float, reason: str) -> None:
        cur = cands.get(tid)
        if cur is None:
            cands[tid] = {"base": base, "reasons": [reason]}
        else:
            cur["base"] = max(cur["base"], base)
            if reason not in cur["reasons"]:
                cur["reasons"].append(reason)

    def candidates(self, t: Table, colname: str, stem: str) -> dict:
        c: dict = {}
        words = stem.split("_")
        for key in ["_".join(words[i:]) for i in range(len(words))]:
            ref = self.o.aliases.get(key)
            if ref:
                tid = _ref(ref, self.s, t.schema)
                if tid:
                    self._add(c, tid, 0.9, f"alias '{key}' -> {tid} (user override)")
        if stem in self.o.role_aliases or stem in BY_ROLES or stem.endswith("_by"):
            names = list(self.o.role_aliases.get(stem, [])) + USER_LIKE
            for nm in names:
                tid = _ref(nm, self.s, t.schema)
                if tid:
                    self._add(c, tid, 0.65, f"role column '{colname}' reads as a user reference; mapped to {tid}")
                    break
        if words[0] in SELF_ROLES and (len(words) == 1 or any(
                tid == t.id for f in forms("_".join(words[1:])) for tid, _ in self.lookup.get(f, []))):
            self._add(c, t.id, 0.8 if words[0] == "parent" else 0.7,
                      f"self-reference: '{colname}' points at this table's own key")
        if colname.lower() in LOG_COLUMNS:
            tid = self._log_subject(t)
            if tid:
                self._add(c, tid, 0.70, f"log table '{t.name}' names its subject table; '{colname}' points at it")
        for f in forms(stem):
            for tid, pref in self.lookup.get(f, []):
                self._add(c, tid, 0.72 if not pref else 0.68,
                          f"column name '{colname}' matches table name"
                          + (f" (prefix '{pref}' stripped)" if pref else ""))
        for i in range(1, len(words)):
            rest = "_".join(words[i:])
            allrole = all(w in self.o.role_words for w in words[:i])
            for f in forms(rest):
                for tid, pref in self.lookup.get(f, []):
                    self._add(c, tid, 0.60 if allrole else 0.50,
                              f"role prefix '{'_'.join(words[:i])}' stripped, '{rest}' matches table name")
        tparts = t.name.lower().split("_")
        for i in range(len(tparts) - 1, 0, -1):  # ad_campaign_banners.campaign_id -> ad_campaigns (shared name prefix)
            pre = "_".join(tparts[:i])
            if pre.endswith("_" + stem) or pre == stem:
                cands = [pre]
            else:
                cands = [pre + "_" + stem]
            hits = {tid for x in cands for f in forms(x) for tid, _ in self.lookup.get(f, [])
                    if tid != t.id and tid.startswith(t.schema + ".")}
            for tid in sorted(hits):
                self._add(c, tid, 0.68, f"column name '{colname}' matches table name with this table's prefix '{pre}_'")
            if hits:
                break
        for tid in self.pkname.get(colname.lower(), []):
            if tid != t.id:
                self._add(c, tid, 0.70, f"column name '{colname}' equals the primary key name of {tid}")
        if not c:
            tails = {tid for f in forms(stem) for tid in self.suffix_lookup.get(f, []) if tid.startswith(t.schema + ".")}
            if len(tails) == 1:  # several tail matches is noise (e.g. every '*_users' table), so none is kept
                tid = next(iter(tails))
                self._add(c, tid, 0.35, f"'{stem}' matches the tail of table name {tid} (weak)")
        c.pop(t.id, None) if not any("self-reference" in r for r in c.get(t.id, {}).get("reasons", [])) else None
        return c

    # --- scoring --------------------------------------------------------------
    def score(self, t: Table, colname: str, stem: str, tid: str, cand: dict):
        tt = self.s.tables[tid]
        tk = target_key(tt, stem, colname)
        if not tk:
            return None
        tcols, tdesc, tboost = tk
        fc, tc = t.column(colname), tt.column(tcols[0])
        ev = list(cand["reasons"])
        conf = cand["base"]
        ff, tf = type_family(fc.type if fc else ""), type_family(tc.type if tc else "")
        if ff == "unknown" or tf == "unknown":
            pass
        elif ff == tf:
            conf += 0.10
            ev.append(f"compatible types ({ff} ~ {tf})")
            if ff == "string" and type_length(fc.type) and type_length(fc.type) == type_length(tc.type):
                conf += 0.03
        elif {ff, tf} <= {"int", "decimal", "float", "bool"}:
            conf -= 0.15
            ev.append(f"loosely compatible types ({ff} vs {tf})")
        else:
            conf -= 0.35
            ev.append(f"type mismatch ({fc.type} vs {tc.type})")
        ix = _is_indexed_leading(t, [colname])
        if ix:
            conf += 0.07
            ev.append(f"source column is the leading column of index '{ix}'")
        conf += tboost
        ev.append(f"target is the {tdesc} of {tid}: {','.join(tcols)}")
        if t.schema == tt.schema:
            conf += 0.05
        else:
            conf -= 0.10
            ev.append("cross-schema match")
        return {"to": tid, "to_columns": tcols, "confidence": conf, "evidence": ev,
                "partial": tboost < 0}

    def _cross_schema(self, t: Table, scored: list[dict]) -> list[dict]:
        """No candidate is in the column's own schema. Candidates in a related schema (ods_app for app) win over
        unrelated ones; then candidates in a declared copy schema lose a little to non-copy ones."""
        if not scored:
            return scored
        related = [r for r in scored if related_schemas(t.schema, r["to"].split(".")[0])]
        if related:
            for r in related:
                r["confidence"] += 0.06
                r["evidence"].append(f"target is in related schema '{r['to'].split('.')[0]}'")
            scored = related
        copies = [r for r in scored if is_copy_schema(r["to"].split(".")[0], self.o.copy_schemas)]
        if copies and len(copies) < len(scored):
            for r in copies:
                r["confidence"] -= 0.05
                r["evidence"].append("target schema is a declared copy")
                r["_copy"] = True
        return scored

    def run(self) -> list[dict]:
        edges: list[dict] = []
        poly_cols: dict[str, list[dict]] = {}
        unresolved: dict[str, list[str]] = {}
        for t in self.s.tables.values():
            declared_from = {c for fk in t.foreign_keys for c in fk.columns}
            colnames = {c.name.lower() for c in t.columns}
            for col in t.columns:
                if col.name in declared_from:
                    continue
                stem = extract_stem(col.name)
                if stem is None:
                    continue
                base = snake(col.name)[:-3] if snake(col.name).endswith("_id") else stem
                pol = [base + "_" + s for s in POLY_SUFFIXES if (base + "_" + s) in colnames]
                if pol:
                    poly_cols.setdefault(t.id, []).append(
                        {"id_column": col.name, "type_column": next(c.name for c in t.columns if c.name.lower() == pol[0]),
                         "note": "polymorphic: the type column decides the target table; no single target"})
                    continue
                scored = []
                for tid, cand in self.candidates(t, col.name, stem).items():
                    r = self.score(t, col.name, stem, tid, cand)
                    if r:
                        scored.append(r)
                if scored and any(r["to"].split(".")[0] == t.schema for r in scored):
                    scored = [r for r in scored if r["to"].split(".")[0] == t.schema] or scored
                else:
                    scored = self._cross_schema(t, scored)
                scored.sort(key=lambda r: (-r["confidence"], r["to"]))
                scored = scored[: self.o.top_k]
                rivals = [r for r in scored if not r.get("_copy")] if any(r.get("_copy") for r in scored) else scored
                amb = len(rivals) >= 2 and rivals[0]["confidence"] - rivals[1]["confidence"] < 0.12
                kept = 0
                for r in scored:
                    r.pop("_copy", None) if r not in rivals else None
                    if amb and r in rivals:
                        r["confidence"] -= 0.10
                        r["evidence"].append(f"ambiguous: {len(scored)} candidate targets within a close score")
                    r["confidence"] = round(max(0.05, min(0.95, r["confidence"])), 2)
                    if r["confidence"] < self.o.min_confidence:
                        continue
                    kept += 1
                    edges.append({
                        "kind": "inferred", "status": "inferred", "from": t.id, "from_columns": [col.name],
                        "to": r["to"], "to_columns": r["to_columns"], "confidence": r["confidence"],
                        "cardinality": "many-to-many" if r["partial"] else cardinality(t, [col.name]),
                        "ambiguous": amb, "evidence": r["evidence"],
                    })
                if not kept:
                    unresolved.setdefault(t.id, []).append(col.name)
        self.polymorphic, self.unresolved = poly_cols, unresolved
        return edges


def declared_edges(schema: Schema, warnings: list[str]) -> list[dict]:
    out = []
    for t in schema.tables.values():
        for fk in t.foreign_keys:
            tid = f"{fk.ref_schema or t.schema}.{fk.ref_table}"
            if tid not in schema.tables:
                warnings.append(f"declared FK {t.id}({','.join(fk.columns)}) references unknown table {tid}")
                continue
            out.append({
                "kind": "declared", "status": "declared", "from": t.id, "from_columns": list(fk.columns),
                "to": tid, "to_columns": list(fk.ref_columns), "confidence": 1.0,
                "cardinality": cardinality(t, list(fk.columns)), "ambiguous": False,
                "evidence": [f"declared foreign key {fk.name or ''}".strip()], "constraint": fk.name or "",
            })
    return out


def redundant_indexes(t: Table) -> list[dict]:
    """Indexes that add nothing: a strict prefix of a longer index, or a duplicate of an equal one."""
    out = []
    ixs = [ix for ix in t.indexes if not ix.approximate]

    def rank(ix):
        return (0 if ix.primary else 1 if ix.unique else 2, ix.name)

    for a in ixs:
        if a.primary:
            continue
        la = len(a.columns)
        for b in ixs:
            if a is b or a.columns != b.columns[:la]:
                continue
            if len(b.columns) == la:
                if rank(b) >= rank(a):
                    continue
                reason = "duplicate of another index on the same columns"
            else:
                if a.unique:
                    continue
                reason = "prefix of a longer index"
            out.append({"index": a.name, "columns": a.columns, "covered_by": b.name,
                        "covered_by_columns": b.columns, "reason": reason})
            break
    return out
