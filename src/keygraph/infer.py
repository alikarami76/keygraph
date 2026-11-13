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


GENERIC_KEY_NAMES = {"name", "title", "code", "email", "login", "username", "slug", "label", "description",
                     "type", "status", "phone", "url", "key", "value", "sku", "mobile", "phone_number",
                     "email_address", "ip_address", "version", "path", "domain", "hostname"}  # often unique, rarely references
PK_ONLY_KEY_NAMES = {"uuid", "guid", "token", "hash", "ip", "number", "external_id", "checksum"}  # references only when the target is a primary key
GENERIC_SUFFIXES = ("_name", "_title", "_description", "_label")  # full_name, display_name: attributes
REF_PREFIXES = ("link_to_", "ref_", "fk_", "id_")
EXTRA_ROLE_WORDS = {"home", "away"}  # only for numbered columns (home_player_1)


def reference_stem(col: str) -> tuple[str, str] | None:
    """link_to_member / ref_member / fk_member / id_member -> ('member', 'link_to_'), for names with no _id suffix."""
    n = snake(col)
    for p in REF_PREFIXES:
        if n.startswith(p) and len(n) - len(p) >= 2:
            return n[len(p):], p
    return None


def numbered_stem(col: str) -> str | None:
    """home_player_1 / awayPlayer2 -> 'home_player' (the name without its trailing number), else None."""
    m = re.fullmatch(r"([a-z][a-z_]*?[a-z])_?(\d+)", snake(col))
    return m.group(1) if m else None


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
        self.keyname: dict[str, set[str]] = {}  # single-column PK or UNIQUE key name -> tables
        self.idpk: dict[str, list[str]] = {}  # schema -> tables whose single-column PK is named id
        self.idpk_name: dict[tuple[str, str], list[str]] = {}  # (schema, singular table name) -> those tables
        self.name_schemas: dict[str, set[str]] = {}  # table name -> schemas that have it
        self.extra_rules = True  # rules a, c, d, e (tests switch them off to prove old edges are unchanged)
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
            self.name_schemas.setdefault(name, set()).add(t.schema)
            if len(pk) == 1 and pk[0].lower() == "id":
                self.idpk.setdefault(t.schema, []).append(t.id)
                self.idpk_name.setdefault((t.schema, singular(name)), []).append(t.id)
            if len(pk) == 1 and pk[0].lower() != "id":
                self.pkname.setdefault(pk[0].lower(), []).append(t.id)
            for k in ([pk] if len(pk) == 1 else []) + [u for u in t.unique_keys if len(u) == 1]:
                if k[0].lower() != "id" and len(k[0]) >= 3:
                    self.keyname.setdefault(k[0].lower(), set()).add(t.id)

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
    def _add(self, cands: dict, tid: str, base: float, reason: str, key=None) -> None:
        cur = cands.get(tid)
        if cur is None:
            cands[tid] = {"base": base, "reasons": [reason]}
            cur = cands[tid]
        else:
            cur["base"] = max(cur["base"], base)
            if reason not in cur["reasons"]:
                cur["reasons"].append(reason)
        if key:  # a rule that names the exact target key (target_key would pick the primary key instead)
            cur["key"] = key

    def _add_rule(self, c: dict, tid: str, base: float, reason: str, key) -> None:
        """Add a candidate from rules a/c/d/e, never touching one that older rules already found. A target keeps
        one key: a later rule with another key is dropped, except that it replaces rule a (a name guess)."""
        cur = c.get(tid)
        if cur is not None and any("(rule:" not in r and "numbered role column" not in r for r in cur["reasons"]):
            return
        if cur is not None and cur.get("key") and key and cur["key"][0] != key[0]:
            if "(rule: shared key name)" in reason:
                return
            if all("(rule: shared key name)" in r for r in cur["reasons"]):
                del c[tid]
            else:
                return
        self._add(c, tid, base, reason, key)

    @staticmethod
    def _single_keys(tt: Table) -> list[tuple[list[str], str, float]]:
        out = [(tt.primary_key, "primary key", 0.08)] if len(tt.primary_key) == 1 else []
        return out + [(u, "unique key", 0.03) for u in tt.unique_keys if len(u) == 1]

    def _extras(self, t: Table, colname: str, c: dict) -> None:
        """Rules that do not need an '<x>_id' stem: shared key name (a), numbered roles (c), table_column (d), bare id (e)."""
        if not self.extra_rules:
            return
        low, n = colname.lower(), snake(colname)
        if low != "id" and len(low) >= 3 and low not in GENERIC_KEY_NAMES:  # (a) shared key name
            others: list[str] = []
            for x in self.keyname.get(low, ()):  # stop at two: more than one other table means no edge
                if x == t.id:
                    continue
                xs = self.s.tables[x].schema
                if xs == t.schema or not (related_schemas(t.schema, xs) or is_copy_schema(t.schema, self.o.copy_schemas)
                                          or is_copy_schema(xs, self.o.copy_schemas)):
                    others.append(x)
                    if len(others) > 1:
                        break
            if len(others) == 1 and not any("primary key name" in r for r in c.get(others[0], {}).get("reasons", [])):
                tt = self.s.tables[others[0]]
                k = next(k for k in self._single_keys(tt) if k[0][0].lower() == low)
                if (low in PK_ONLY_KEY_NAMES or low.endswith(GENERIC_SUFFIXES)) and k[1] != "primary key":
                    others = []
            if len(others) == 1 and not any("primary key name" in r for r in c.get(others[0], {}).get("reasons", [])):
                self._add_rule(c, tt.id, 0.70, f"column name '{colname}' equals the key column of {tt.id} (rule: shared key name)", k)
        num = numbered_stem(colname)  # (c) numbered role columns
        if num:
            words = num.split("_")
            if words[-1] == "id" and len(words) > 1:
                words = words[:-1]
            roles = self.o.role_words | EXTRA_ROLE_WORDS
            while len(words) > 1 and words[0] in roles:
                words = words[1:]
            stem = "_".join(words)
            for f in forms(stem):
                for tid, _ in self.lookup.get(f, []):
                    tt = self.s.tables[tid]
                    if tid == t.id:
                        continue
                    pre = {x + "_" for x in forms(stem)}
                    pk = tt.primary_key
                    ukeys = [u for u in tt.unique_keys if len(u) == 1 and u[0].lower().startswith(tuple(pre)) and u[0].lower().endswith("_id")]
                    note = ""
                    if len(pk) == 1 and pk[0].lower().startswith(tuple(pre)):
                        k = (pk, "primary key", 0.08)
                    elif len(ukeys) > 1 and len(pk) == 1 and pk[0].lower() == "id":
                        names = ", ".join(sorted(u[0] for u in ukeys))
                        self._add_rule(c, tid, 0.60, f"numbered role column '{colname}' reads as a reference to '{stem}'; "
                                       f"several named keys could be the target: {names}", (ukeys[0], "unique key", 0.03))
                        c[tid]["multi"] = sorted(ukeys)
                        continue
                    elif len(ukeys) == 1 and len(pk) == 1 and pk[0].lower() == "id":
                        k = (ukeys[0], "unique key", 0.03)
                        note = (f"; target table has a surrogate id and a named key '{ukeys[0][0]}'; "
                                "numbered role columns usually hold the named key")
                    elif len(ukeys) == 1 and not pk:
                        k = (ukeys[0], "unique key", 0.03)
                    elif len(pk) == 1:
                        k = (pk, "primary key", 0.08)
                    else:
                        continue
                    self._add_rule(c, tid, 0.60, f"numbered role column '{colname}' reads as a reference to '{stem}'{note}", k)
        parts = n.split("_")  # (d) <table>_<column>
        for i in range(1, len(parts)):
            cname = "_".join(parts[i:])
            if cname == "id":
                continue
            hits = [tid for f in forms("_".join(parts[:i])) for tid, _ in self.lookup.get(f, [])
                    if tid != t.id and self.s.tables[tid].schema == t.schema]
            if len(set(hits)) != 1:
                continue
            tt = self.s.tables[hits[0]]
            for k in self._single_keys(tt):
                if snake(k[0][0]) == cname:
                    if (cname in GENERIC_KEY_NAMES or cname in PK_ONLY_KEY_NAMES) and k[1] != "primary key":
                        continue  # orders.user_email -> users.email is a copied attribute, not a reference
                    self._add_rule(c, tt.id, 0.68, f"column name '{colname}' reads as {tt.name}.{k[0][0]} (rule: table_column)", k)
        if low == "id" and not t.primary_key and not t.unique_keys:  # (e) bare id without a key of its own
            toks = {singular(w) for w in snake(t.name).split("_")}
            named = sorted({x for w in toks for x in self.idpk_name.get((t.schema, w), []) if x != t.id})
            idt = [x for x in self.idpk.get(t.schema, []) if x != t.id]
            copy = len(self.name_schemas.get(t.name.lower(), ())) > 1
            for xid in named or (idt if len(idt) == 1 and not copy else []):  # no name match: not for a copy of another schema's table
                x = self.s.tables[xid]
                self._add_rule(c, x.id, 0.62, f"bare 'id' in a table with no key of its own; {x.id} is the table keyed by id"
                               + (" and its name is part of this table's name" if named else "") + " (rule: bare id)",
                               (x.primary_key, "primary key", 0.08))

    def candidates(self, t: Table, colname: str, stem: str, refpref: str = "") -> dict:
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
                self._add(c, tid, 0.72 if not (pref or refpref) else 0.68,
                          f"column name '{colname}' matches table name"
                          + (f" (prefix '{pref}' stripped)" if pref else "")
                          + (f" (reference prefix '{refpref}' stripped)" if refpref else ""))
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
        self._extras(t, colname, c)
        c.pop(t.id, None) if not any("self-reference" in r for r in c.get(t.id, {}).get("reasons", [])) else None
        return c

    # --- scoring --------------------------------------------------------------
    def score(self, t: Table, colname: str, stem: str, tid: str, cand: dict):
        tt = self.s.tables[tid]
        tk = cand.get("key") or target_key(tt, stem, colname)
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
        reasons = cand["reasons"]
        exact = bool(fc and tc and fc.type.strip().lower() == tc.type.strip().lower())
        shared = [r for r in reasons if "(rule: shared key name)" in r]
        others = [r for r in reasons if "numbered role column" in r or "(rule: bare id)" in r or "(rule: table_column)" in r]
        if shared and len(shared) + len(others) == len(reasons):  # only new naming rules: the strictest cap wins
            if [colname] in ([t.primary_key] + t.unique_keys):
                if conf > 0.55:
                    conf = 0.55
                    ev.append("both columns are keys of their own tables; a shared name alone is weak")
            elif conf > 0.75:
                conf = 0.75
                ev.append("capped at 0.75: a shared name alone is not proof of a reference")
            if others and any("its name is part of this table's name" in r for r in others) and conf > 0.70:
                conf = 0.70
                ev.append("capped at 0.70: a keyless table named after another table's word is a weak signal")
        elif all("(reference prefix 'id_' stripped)" in r or "(reference prefix 'ref_' stripped)" in r for r in reasons):
            if conf > 0.75 and not (exact and ix):  # id_card, ref_code, id_type are often attributes, not references
                conf = 0.75
                ev.append("capped at 0.75: no exact type match plus an index on the source column")
        elif others and len(others) == len(reasons):
            if any("its name is part of this table's name" in r for r in reasons) and conf > 0.70:
                conf = 0.70
                ev.append("capped at 0.70: a keyless table named after another table's word is a weak signal")
            elif conf > 0.75 and not (exact and ix):
                conf = 0.75  # a naming guess alone never reads as 0.8 or more
                ev.append("capped at 0.75: no exact type match plus an index on the source column")
        if cand.get("multi") and conf > 0.55:
            conf = 0.55
            ev.append("capped at 0.55: several candidate keys, none preferred")
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
                stem, refpref = extract_stem(col.name), ""
                if stem is None and (rs := reference_stem(col.name)):
                    stem, refpref = rs
                if stem is None:
                    extra: dict = {}
                    self._extras(t, col.name, extra)
                    extra.pop(t.id, None)
                    cand_map = extra
                else:
                    cand_map = None
                base = snake(col.name)[:-3] if snake(col.name).endswith("_id") else stem
                pol = [base + "_" + s for s in POLY_SUFFIXES if (base + "_" + s) in colnames] if cand_map is None else []
                if pol:
                    poly_cols.setdefault(t.id, []).append(
                        {"id_column": col.name, "type_column": next(c.name for c in t.columns if c.name.lower() == pol[0]),
                         "note": "polymorphic: the type column decides the target table; no single target"})
                    continue
                scored = []
                for tid, cand in (cand_map if cand_map is not None else self.candidates(t, col.name, stem, refpref)).items():
                    for k in cand.get("multi") or [None]:
                        r = self.score(t, col.name, stem, tid, dict(cand, key=(k, "unique key", 0.03)) if k else cand)
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
                if not kept and cand_map is None:
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
