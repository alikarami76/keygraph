"""Business context stored relationally in SQLite (with FTS5 search): concepts, links, relations,
column semantics, metrics and table notes. Every row records its source, a verified flag and a timestamp."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

_DIGITS = {**{0x0660 + i: 0x30 + i for i in range(10)}, **{0x06F0 + i: 0x30 + i for i in range(10)}}
_MAP = {0x064A: 0x06CC, 0x0649: 0x06CC, 0x0643: 0x06A9, 0x200C: 0x20, 0x0640: None, 0x06C0: 0x0647, 0x0629: 0x0647}
for _c in range(0x064B, 0x0660):
    _MAP[_c] = None


def normalize(text: str) -> str:
    """Lowercase, unify Persian/Arabic digits, ye/kaf variants, drop diacritics, split snake/camel/dotted names."""
    text = str(text or "")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = text.translate(_DIGITS).translate(_MAP).lower()
    return re.sub(r"[\s_./\\:;,|()\[\]{}\-]+", " ", text).strip()


STOPWORDS = frozenset("a an and are as at be by for from in into is of on or per the to with".split())
PHRASE_BOOST = 2.0


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", normalize(text))


SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts(name TEXT PRIMARY KEY, definition TEXT, domain TEXT, owner TEXT, synonyms TEXT,
  source TEXT, verified INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS concept_links(concept TEXT, table_id TEXT, col TEXT, role TEXT, note TEXT, source TEXT,
  verified INTEGER, updated_at TEXT, PRIMARY KEY(concept, table_id, col, role));
CREATE TABLE IF NOT EXISTS concept_relations(from_concept TEXT, rel TEXT, to_concept TEXT, edge_id TEXT, note TEXT,
  source TEXT, verified INTEGER, updated_at TEXT, PRIMARY KEY(from_concept, rel, to_concept));
CREATE TABLE IF NOT EXISTS column_semantics(table_id TEXT, col TEXT, meaning TEXT, unit TEXT, codes TEXT, null_meaning TEXT,
  pii INTEGER, typical_filters TEXT, index_hint TEXT, source TEXT, verified INTEGER, updated_at TEXT,
  PRIMARY KEY(table_id, col));
CREATE TABLE IF NOT EXISTS metrics(name TEXT PRIMARY KEY, definition TEXT, expression TEXT, grain TEXT, tables TEXT,
  filters TEXT, caveats TEXT, source TEXT, verified INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS table_notes(table_id TEXT PRIMARY KEY, grain TEXT, freshness TEXT, soft_delete TEXT,
  date_column TEXT, recommended_indexes TEXT, pitfalls TEXT, summary TEXT, source TEXT, verified INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS column_profile(table_id TEXT, col TEXT, profile TEXT, profiled_at TEXT, sample_size INTEGER, method TEXT,
  PRIMARY KEY(table_id, col));
CREATE TABLE IF NOT EXISTS column_details(table_id TEXT, col TEXT, detail TEXT, PRIMARY KEY(table_id, col));
CREATE TABLE IF NOT EXISTS table_info(table_id TEXT PRIMARY KEY, info TEXT);
CREATE TABLE IF NOT EXISTS context_history(kind TEXT, ref TEXT, action TEXT, old TEXT, new TEXT, source TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS curation(kind TEXT, body JSON, source TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(kind, ref, title, body, tokenize='unicode61');
"""

KINDS = {"concept", "relation", "link", "column", "metric", "table"}
ROLES = ("primary-record", "attribute", "foreign-reference", "measure", "filter")


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False) if v not in (None, "") else ""


def _l(v) -> list:
    if v in (None, ""):
        return []
    return v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]


def safe_source(src) -> str:
    """A context source as it may appear in outputs: never an absolute local path (keep the bare file name)."""
    s = str(src or "").strip()
    if s.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:[\\/]", s):
        return re.split(r"[\\/]", s.rstrip("/\\"))[-1]
    return s


def copy_globs_split(raw: dict) -> tuple[dict, list]:
    """A context file's mirrors mapping and its copy-schema globs. Globs come from a top-level `copies:` list or
    from a `copies:` list inside `mirrors:` (a schema literally named copies can still be written as copies: orig)."""
    mirrors = raw.get("mirrors")
    globs: list = []
    if isinstance(mirrors, dict) and isinstance(mirrors.get("copies"), (list, str)):
        mirrors = dict(mirrors)
        v = mirrors.pop("copies")
        globs += [v] if isinstance(v, str) else list(v)
    top = raw.get("copies")
    globs += [top] if isinstance(top, str) else list(top or []) if isinstance(top, list) else []
    return (mirrors if mirrors else {}), [g for g in dict.fromkeys(globs) if g]


class YamlMissing(ValueError):
    """A YAML context file was given but the optional PyYAML extra is not installed."""


def _need_yaml(path=None):
    """Import PyYAML or raise a clear error (it is an optional extra)."""
    try:
        import yaml
    except ImportError:
        raise YamlMissing(f"reading YAML context files{f' ({Path(path).name})' if path else ''} needs PyYAML: "
                          "pip install 'keygraph[yaml]', or write the file as JSON") from None
    return yaml


def read_copy_globs(paths) -> list[str]:
    """Copy-schema globs declared in context files, read before the graph is built (inference needs them).
    Unreadable files are skipped here; the later import reports them. A YAML file without PyYAML is an error."""
    out: list[str] = []
    for path in paths or []:
        p = Path(path)
        for f in sorted(f for f in ([p] if p.is_file() else p.rglob("*")) if f.suffix.lower() in (".yaml", ".yml", ".json")):
            try:
                text = f.read_text(encoding="utf-8")
                if f.suffix.lower() == ".json":
                    raw = json.loads(text)
                else:
                    raw = _need_yaml(f).safe_load(text) or {}
                out += [g for g in copy_globs_split(raw)[1] if g not in out] if isinstance(raw, dict) else []
            except YamlMissing:
                raise
            except Exception:  # noqa: BLE001
                continue
    return out


class ContextDB:
    def __init__(self, path: str | Path, graph=None, readonly: bool = False):
        """readonly: open an existing file without writing to it; a missing file gives an empty in-memory context."""
        self.path = Path(path)
        if readonly and self.path.exists():
            self.db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        elif readonly:
            self.db = sqlite3.connect(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        if not (readonly and self.path.exists()):
            self.db.executescript(SCHEMA)
        else:  # a file from an older keygraph may lack newer tables: read them as empty (temp tables, file untouched)
            have = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master")}
            for stmt in SCHEMA.split(";"):
                m = re.search(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS (\w+)", stmt)
                if m and m.group(1) not in have:
                    self.db.execute(stmt.replace("IF NOT EXISTS ", "IF NOT EXISTS temp.", 1))
        self.graph = graph
        self.warnings: list[str] = []
        self.kept: dict[str, int] = {}  # verified rows kept against an unverified write, by kind

    def close(self) -> None:
        try:
            self.db.commit()
        finally:
            self.db.close()

    def __enter__(self) -> "ContextDB":
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self.close()
        else:  # leave half-done writes uncommitted, but always release the handle
            self.db.close()

    # --- reference checks --------------------------------------------------------
    def _tid(self, ref: str | None) -> str:
        if not ref or not self.graph:
            return ref or ""
        try:
            return self.graph.resolve(ref)
        except ValueError as exc:
            self.warnings.append(f"unknown table '{ref}': {exc}")
            return ref

    def _check_col(self, tid: str, col: str | None) -> None:
        if col and self.graph and tid in self.graph.nodes and col not in {c["name"] for c in self.graph.nodes[tid]["columns"]}:
            self.warnings.append(f"unknown column {tid}.{col}")

    # --- writes ------------------------------------------------------------------
    def _meta(self, d: dict, default_source: str, default_verified) -> tuple:
        v = d.get("verified", default_verified)
        return (safe_source(d.get("source") or default_source) or "unknown", 1 if v in (True, 1, "true", "yes") else 0,
                time.strftime("%Y-%m-%dT%H:%M:%S"))

    def _keeps_verified(self, kind: str, table: str, keys: dict, label: str, src: tuple, same_ok: bool = False) -> bool:
        """True (with a warning) when an unverified write would replace a verified row.
        Only a file import (same_ok) may update its own rows; a verified write replaces anything."""
        if src[1]:
            return False
        where = " AND ".join(f"{k}=?" for k in keys)
        old = self.db.execute(f"SELECT source, verified FROM {table} WHERE {where}", tuple(keys.values())).fetchone()
        old_src = safe_source(old["source"]) if old else ""
        if not old or not old["verified"] or (same_ok and old_src == src[0]):
            return False
        self.kept[kind] = self.kept.get(kind, 0) + 1
        self.warnings.append(f"kept verified {kind} note for {label} (source {old_src}); unverified write from {src[0]} ignored; "
                              "pass --verified (or verified=true) if you have confirmed it")
        return True

    def _put(self, kind: str, table: str, keys: dict, label: str, values: tuple, src: tuple, same_ok: bool = False) -> bool:
        """Write one row (INSERT OR REPLACE) unless a verified row is kept; record a history row when a verified
        write is refused ("kept") or an existing row's content changes ("replaced"). Identical rewrites leave no trace."""
        cols = [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]
        new = dict(zip(cols, values))
        where = " AND ".join(f"{k}=?" for k in keys)
        old = self.db.execute(f"SELECT * FROM {table} WHERE {where}", tuple(keys.values())).fetchone()
        old_d = dict(old) if old else None
        if old_d and old_d.get("source"):
            old_d["source"] = safe_source(old_d["source"])
        if self._keeps_verified(kind, table, keys, label, src, same_ok):
            skip = ("source", "verified", "updated_at")
            last = self.db.execute("SELECT new FROM context_history WHERE kind=? AND ref=? AND action='kept' ORDER BY rowid DESC LIMIT 1",
                                   (kind, label)).fetchone()
            body = {k: v for k, v in new.items() if k not in skip}
            if not (last and {k: v for k, v in json.loads(last[0]).items() if k not in skip} == body):
                self._history(kind, label, "kept", old_d, new, src[0])
            return False
        self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES({','.join('?' * len(values))})", values)
        if old_d and any(old_d[c] != new[c] for c in cols if c != "updated_at"):
            self._history(kind, label, "replaced", old_d, new, src[0])
        return True

    def _history(self, kind: str, ref: str, action: str, old, new, source: str) -> None:
        self.db.execute("INSERT INTO context_history VALUES(?,?,?,?,?,?,?)",
                        (kind, ref, action, *(json.dumps(d, ensure_ascii=False, sort_keys=True, default=str) for d in (old, new)),
                         source, time.strftime("%Y-%m-%dT%H:%M:%S")))

    def history(self, limit: int = 50, ref: str | None = None, kind: str | None = None) -> list[dict]:
        """Overwritten and refused context writes, newest first. old/new are the rows as dicts."""
        where, args = [], []
        for col, v in (("ref", ref), ("kind", kind)):
            if v:
                where.append(f"{col}=?")
                args.append(v)
        q = "SELECT * FROM context_history" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY rowid DESC LIMIT ?"
        out = []
        for r in self.db.execute(q, (*args, int(limit))):
            d = dict(r)
            d["old"], d["new"] = json.loads(d["old"]) if d["old"] else None, json.loads(d["new"]) if d["new"] else None
            out.append(d)
        return out

    def upsert(self, kind: str, d: dict, source: str = "", verified=False,
               allow_same_source: bool = False) -> bool:
        """Write one context item; False when a verified row was kept instead. allow_same_source (file imports only)
        lets an unverified write update a verified row that came from the same source."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind '{kind}'; use one of {sorted(KINDS)}")
        src = self._meta(d, source, verified)
        x = self.db
        if kind == "concept":
            name = str(d["name"]).strip()
            written = self._put(kind, "concepts", {"name": name}, name,
                                (name, d.get("definition", ""), d.get("domain", ""), d.get("owner", ""), _j(_l(d.get("synonyms"))), *src),
                                src, allow_same_source)
            for ln in d.get("links") or []:
                self.upsert("link", {**ln, "concept": name, "source": ln.get("source") or d.get("source"),
                                     "verified": ln.get("verified", d.get("verified"))}, source, verified, allow_same_source)
        elif kind == "link":
            tid = self._tid(d["table"])
            col = d.get("column") or ""
            self._check_col(tid, col)
            role = d.get("role") or ("primary-record" if not col else "attribute")
            if role not in ROLES:
                self.warnings.append(f"link role '{role}' is not one of {ROLES}")
            if not self._put(kind, "concept_links", {"concept": d["concept"], "table_id": tid, "col": col, "role": role},
                             f"{d['concept']} -> {tid}{'.' + col if col else ''} ({role})",
                             (d["concept"], tid, col, role, d.get("note", ""), *src), src, allow_same_source):
                return False
        elif kind == "relation":
            rel = d.get("rel", "related-to")
            if not self._put(kind, "concept_relations", {"from_concept": d["from"], "rel": rel, "to_concept": d["to"]},
                             f"{d['from']} {rel} {d['to']}",
                             (d["from"], rel, d["to"], d.get("edge", ""), d.get("note", ""), *src), src, allow_same_source):
                return False
        elif kind == "column":
            tid = self._tid(d["table"])
            self._check_col(tid, d["column"])
            if not self._put(kind, "column_semantics", {"table_id": tid, "col": d["column"]}, f"{tid}.{d['column']}",
                             (tid, d["column"], d.get("meaning", ""), d.get("unit", ""), _j(d.get("codes") or {}),
                              d.get("null_meaning", ""), 1 if d.get("pii") in (True, 1, "true", "yes") else 0,
                              _j(d.get("typical_filters", "")), d.get("index_hint", ""), *src), src, allow_same_source):
                return False
        elif kind == "metric":
            tbls = [self._tid(t) for t in _l(d.get("tables"))]
            if not self._put(kind, "metrics", {"name": d["name"]}, d["name"],
                             (d["name"], d.get("definition", ""), d.get("expression", ""), d.get("grain", ""), _j(tbls),
                              d.get("filters", ""), d.get("caveats", ""), *src), src, allow_same_source):
                return False
        elif kind == "table":
            tid = self._tid(d["table"])
            self._check_col(tid, d.get("date_column"))
            if not self._put(kind, "table_notes", {"table_id": tid}, tid,
                             (tid, d.get("grain", ""), d.get("freshness", ""), d.get("soft_delete", ""), d.get("date_column", ""),
                              _j(_l(d.get("recommended_indexes"))), d.get("pitfalls", ""), d.get("summary", ""), *src),
                             src, allow_same_source):
                return False
        x.commit()
        return written if kind == "concept" else True

    def import_path(self, path: str | Path, default_verified=False) -> dict:
        p = Path(path)
        files = sorted(f for f in ([p] if p.is_file() else p.rglob("*")) if f.suffix.lower() in (".yaml", ".yml", ".json"))
        counts = {k: 0 for k in ("concepts", "relations", "columns", "metrics", "tables")}
        for f in files:
            text = f.read_text(encoding="utf-8")
            if f.suffix.lower() == ".json":
                raw = json.loads(text)
            else:
                raw = _need_yaml(f).safe_load(text) or {}
            rel = f.relative_to(p).as_posix() if p.is_dir() else f.name
            if not isinstance(raw, dict):
                raise ValueError(f"context file {rel}: expected a mapping at the top level, got {type(raw).__name__}")
            self._save_curation(rel, raw)
            src, ver = safe_source(raw.get("source")) or rel, raw.get("verified", default_verified)
            for key, kind in (("concepts", "concept"), ("relations", "relation"), ("columns", "column"),
                              ("metrics", "metric"), ("tables", "table")):
                for item in raw.get(key) or []:
                    if self.upsert(kind, item, src, ver, allow_same_source=True):
                        counts[key] += 1
        self.reindex()
        return counts

    def add_copy_schemas(self, globs: list[str], source: str = "--copy-schema") -> None:
        """Remember copy-schema globs given on the command line, next to those from context files."""
        self.db.execute("DELETE FROM curation WHERE source=?", (source,))
        if globs:
            self.db.execute("INSERT INTO curation VALUES('copies',?,?)", (json.dumps(list(globs)), source))
        self.db.commit()

    def clear_curation(self) -> None:
        """Forget all curation rules (a build that imports context starts from the files it imports now)."""
        self.db.execute("DELETE FROM curation")
        self.db.commit()

    def _save_curation(self, source: str, raw: dict) -> None:
        """Replace this file's curation rows, so importing the same file again never duplicates rules."""
        self.db.execute("DELETE FROM curation WHERE source=?", (source,))
        for rule in raw.get("areas") or []:
            self.db.execute("INSERT INTO curation VALUES('area',?,?)", (json.dumps(rule, ensure_ascii=False), source))
        mirrors, copies = copy_globs_split(raw)
        if mirrors:
            self.db.execute("INSERT INTO curation VALUES('mirrors',?,?)", (json.dumps(mirrors, ensure_ascii=False), source))
        if copies:
            self.db.execute("INSERT INTO curation VALUES('copies',?,?)", (json.dumps(copies, ensure_ascii=False), source))
        if raw.get("hubs"):
            self.db.execute("INSERT INTO curation VALUES('hubs',?,?)", (json.dumps(raw["hubs"], ensure_ascii=False), source))
        self.db.commit()

    # --- reads -------------------------------------------------------------------
    def rows(self, table: str, where: str = "", args: tuple = ()) -> list[dict]:
        out = [dict(r) for r in self.db.execute(f"SELECT * FROM {table} {where}", args)]
        for r in out:  # rows written before sources were made relative may still hold absolute paths
            if r.get("source"):
                r["source"] = safe_source(r["source"])
        return out

    def curation(self) -> dict:
        """Area rules, hub and mirror overrides from the imported files. Malformed entries (user-written YAML)
        are skipped with a line in out["warnings"], never raised."""
        out: dict = {"areas": [], "hubs": {"include": [], "exclude": []}, "mirrors": {}, "copies": [], "warnings": []}
        warn = out["warnings"].append
        for r in self.db.execute("SELECT * FROM curation ORDER BY source, rowid"):
            body, src = json.loads(r["body"]), safe_source(r["source"])
            if r["kind"] == "area":
                if not isinstance(body, dict):
                    warn(f"{src}: area rule {body!r} ignored: expected a mapping with name, match or merge")
                elif not any(body.get(k) for k in ("name", "match", "merge")):
                    warn(f"{src}: area rule {body!r} ignored: it needs a name, a match or a merge")
                else:
                    rule = dict(body)
                    for k in ("match", "pin", "exclude", "merge"):
                        if k in rule:
                            rule[k] = [str(x) for x in _l(rule[k]) if x is not None]
                    if "name" in rule:
                        rule["name"] = str(rule["name"])
                    out["areas"].append(rule)
            elif r["kind"] == "copies":  # fnmatch globs of schemas that are copies (replicas, warehouses, BI caches)
                if isinstance(body, list):
                    out["copies"] += [str(g) for g in body if str(g) not in out["copies"]]
                else:
                    warn(f"{src}: copies ignored: expected a list of schema globs")
            elif r["kind"] == "mirrors":  # {mirror schema: original schema, or false for "never a mirror"}
                if isinstance(body, dict):
                    out["mirrors"].update({str(k): v for k, v in body.items()})
                else:
                    warn(f"{src}: mirrors ignored: expected a mapping of mirror schema to original schema")
            elif isinstance(body, dict):
                for k in ("include", "exclude"):
                    out["hubs"][k] += [str(t) for t in _l(body.get(k)) if str(t) not in out["hubs"][k]]
            else:
                warn(f"{src}: hubs ignored: expected a mapping with include and/or exclude lists")
        return out

    def counts(self) -> dict:
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("concepts", "concept_links", "concept_relations", "column_semantics", "metrics", "table_notes")}

    def table_context(self, tid: str) -> dict:
        n = self.rows("table_notes", "WHERE table_id=?", (tid,))
        links = self.rows("concept_links", "WHERE table_id=?", (tid,))
        cols = self.rows("column_semantics", "WHERE table_id=? ORDER BY col", (tid,))
        for c in cols:
            c["codes"] = json.loads(c["codes"]) if c["codes"] else {}
        metrics = [m for m in self.rows("metrics") if tid in json.loads(m["tables"] or "[]")]
        return {"notes": n[0] if n else None, "concepts": links, "columns": cols, "metrics": metrics}

    def dump(self) -> dict:
        out = {"concepts": [], "relations": [], "columns": [], "metrics": [], "tables": []}
        for c in self.rows("concepts", "ORDER BY name"):
            c["synonyms"] = json.loads(c["synonyms"] or "[]")
            c["links"] = [{"table": l["table_id"], "column": l["col"], "role": l["role"], "note": l["note"],
                           "source": safe_source(l["source"]), "verified": bool(l["verified"])}
                          for l in self.rows("concept_links", "WHERE concept=?", (c["name"],))]
            c["verified"] = bool(c["verified"])
            out["concepts"].append(c)
        for r in self.rows("concept_relations", "ORDER BY from_concept,rel,to_concept"):
            out["relations"].append({"from": r["from_concept"], "rel": r["rel"], "to": r["to_concept"], "edge": r["edge_id"],
                                     "note": r["note"], "source": safe_source(r["source"]), "verified": bool(r["verified"])})
        for r in self.rows("column_semantics", "ORDER BY table_id,col"):
            r["codes"] = json.loads(r["codes"]) if r["codes"] else {}
            r["verified"], r["pii"] = bool(r["verified"]), bool(r["pii"])
            r["table"], r["column"] = r.pop("table_id"), r.pop("col")
            out["columns"].append(r)
        for r in self.rows("metrics", "ORDER BY name"):
            r["tables"] = json.loads(r["tables"] or "[]")
            r["verified"] = bool(r["verified"])
            out["metrics"].append(r)
        for r in self.rows("table_notes", "ORDER BY table_id"):
            r["recommended_indexes"] = json.loads(r["recommended_indexes"] or "[]")
            r["verified"] = bool(r["verified"])
            r["table"] = r.pop("table_id")
            out["tables"].append(r)
        return out

    # --- full-text index ---------------------------------------------------------
    def index_graph(self, graph) -> None:
        self.graph = graph
        self.db.execute("DELETE FROM search_fts WHERE kind IN ('table','column')")
        rows = []
        self.db.execute("DELETE FROM column_details")
        self.db.execute("DELETE FROM table_info")
        det, tinfo = [], []
        for n in graph.nodes.values():
            rows.append(("table", n["id"], n["id"], normalize(n["id"] + " " + str((n.get("info") or {}).get("comment", "")))))
            if n.get("info"):
                tinfo.append((n["id"], json.dumps(n["info"], ensure_ascii=False, default=str)))
            for c in n["columns"]:
                extra = " ".join([c.get("comment", ""), " ".join(c.get("values", []))])
                rows.append(("column", f"{n['id']}.{c['name']}", c["name"], normalize(n["table"] + " " + c["name"] + " " + extra)))
                if len(c) > 5:
                    det.append((n["id"], c["name"], json.dumps({k: v for k, v in c.items() if k not in ("name", "family")}, ensure_ascii=False)))
        self.db.executemany("INSERT INTO column_details VALUES(?,?,?)", det)
        self.db.executemany("INSERT OR REPLACE INTO table_info VALUES(?,?)", tinfo)
        self.db.executemany("INSERT INTO search_fts(kind,ref,title,body) VALUES(?,?,?,?)", rows)
        self.db.commit()

    def save_profiles(self, profiles: dict) -> None:
        """profiles: {"schema.table.col": profile dict}. PII profiles never carry raw values (see profile.py)."""
        for key, p in profiles.items():
            tid, col = key.rsplit(".", 1)
            self.db.execute("INSERT OR REPLACE INTO column_profile VALUES(?,?,?,?,?,?)",
                            (tid, col, json.dumps(p, ensure_ascii=False, default=str), p.get("profiled_at"), p.get("sample_size"), p.get("method")))
        self.db.commit()

    def load_profiles(self) -> dict:
        return {f"{r['table_id']}.{r['col']}": json.loads(r["profile"]) for r in self.db.execute("SELECT * FROM column_profile")}

    def reindex(self) -> None:
        self.db.execute("DELETE FROM search_fts WHERE kind NOT IN ('table','column','query')")  # the query log keeps its own rows
        rows = []
        for r in self.db.execute("SELECT * FROM column_profile"):
            p = json.loads(r["profile"])
            if p.get("pii") or not p.get("top_values"):
                continue
            rows.append(("column_value", f"{r['table_id']}.{r['col']}", r["col"], normalize(r["col"] + " " + " ".join(str(k) for k, _ in p["top_values"]))))
        for c in self.rows("concepts"):
            syn = " ".join(json.loads(c["synonyms"] or "[]"))
            rows.append(("concept", c["name"], c["name"], normalize(" ".join([c["name"], syn, c["definition"] or "", c["domain"] or ""]))))
        for m in self.rows("metrics"):
            rows.append(("metric", m["name"], m["name"], normalize(" ".join([m["name"], m["definition"] or "", m["caveats"] or ""]))))
        for t in self.rows("table_notes"):
            rows.append(("table_note", t["table_id"], t["table_id"], normalize(" ".join(
                [t["grain"] or "", t["summary"] or "", t["pitfalls"] or "", t["freshness"] or ""]))))
        for c in self.rows("column_semantics"):
            codes = " ".join(f"{k} {v}" for k, v in json.loads(c["codes"] or "{}").items())
            rows.append(("column_ctx", f"{c['table_id']}.{c['col']}", c["col"], normalize(" ".join(
                [c["col"], c["meaning"] or "", c["unit"] or "", codes, c["null_meaning"] or ""]))))
        self.db.executemany("INSERT INTO search_fts(kind,ref,title,body) VALUES(?,?,?,?)", rows)
        self.db.commit()

    def fts(self, query: str, limit: int = 300) -> list[tuple[str, str, float]]:
        """Prefix OR over the meaningful words, plus a boost for rows holding the whole query as a phrase.

        Words shorter than 3 characters and common stopwords are left out of the prefix OR ("in"* would match
        inventory, invoices and every int column), unless the query has nothing else.
        """
        toks = tokens(query)
        if not toks:
            return []
        words = [t for t in toks if len(t) >= 3 and t not in STOPWORDS]
        q = " OR ".join(f'"{t}"*' for t in words) if words else " OR ".join(f'"{t}"' for t in toks)
        scores: dict[tuple[str, str], float] = {}
        order: list[tuple[str, str]] = []

        def run(match: str, boost: float) -> None:
            try:
                cur = self.db.execute(
                    "SELECT kind, ref, bm25(search_fts, 0.0, 0.0, 3.0, 1.0) AS s FROM search_fts WHERE search_fts MATCH ? AND kind != 'query' "
                    "ORDER BY s LIMIT ?", (match, limit))
            except sqlite3.OperationalError:
                return False
            for r in cur:
                k = (r["kind"], r["ref"])
                if k not in scores:
                    order.append(k)
                scores[k] = scores.get(k, 0.0) + boost * -r["s"]

        run(q, 1.0)
        if len(toks) > 1:
            run('"' + " ".join(toks) + '"', PHRASE_BOOST)
        return sorted(((k, r, scores[(k, r)]) for k, r in order), key=lambda x: -x[2])[:limit]
