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


class ContextDB:
    def __init__(self, path: str | Path, graph=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.graph = graph
        self.warnings: list[str] = []

    def close(self) -> None:
        self.db.commit()
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
        return (d.get("source") or default_source or "unknown", 1 if v in (True, 1, "true", "yes") else 0,
                time.strftime("%Y-%m-%dT%H:%M:%S"))

    def upsert(self, kind: str, d: dict, source: str = "", verified=False) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown kind '{kind}'; use one of {sorted(KINDS)}")
        src = self._meta(d, source, verified)
        x = self.db
        if kind == "concept":
            name = str(d["name"]).strip()
            x.execute("INSERT OR REPLACE INTO concepts VALUES(?,?,?,?,?,?,?,?)",
                      (name, d.get("definition", ""), d.get("domain", ""), d.get("owner", ""), _j(_l(d.get("synonyms"))), *src))
            for ln in d.get("links") or []:
                self.upsert("link", {**ln, "concept": name, "source": ln.get("source") or d.get("source"),
                                     "verified": ln.get("verified", d.get("verified"))}, source, verified)
        elif kind == "link":
            tid = self._tid(d["table"])
            col = d.get("column") or ""
            self._check_col(tid, col)
            role = d.get("role") or ("primary-record" if not col else "attribute")
            if role not in ROLES:
                self.warnings.append(f"link role '{role}' is not one of {ROLES}")
            x.execute("INSERT OR REPLACE INTO concept_links VALUES(?,?,?,?,?,?,?,?)",
                      (d["concept"], tid, col, role, d.get("note", ""), *src))
        elif kind == "relation":
            x.execute("INSERT OR REPLACE INTO concept_relations VALUES(?,?,?,?,?,?,?,?)",
                      (d["from"], d.get("rel", "related-to"), d["to"], d.get("edge", ""), d.get("note", ""), *src))
        elif kind == "column":
            tid = self._tid(d["table"])
            self._check_col(tid, d["column"])
            x.execute("INSERT OR REPLACE INTO column_semantics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (tid, d["column"], d.get("meaning", ""), d.get("unit", ""), _j(d.get("codes") or {}),
                       d.get("null_meaning", ""), 1 if d.get("pii") in (True, 1, "true", "yes") else 0,
                       _j(d.get("typical_filters", "")), d.get("index_hint", ""), *src))
        elif kind == "metric":
            tbls = [self._tid(t) for t in _l(d.get("tables"))]
            x.execute("INSERT OR REPLACE INTO metrics VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (d["name"], d.get("definition", ""), d.get("expression", ""), d.get("grain", ""), _j(tbls),
                       d.get("filters", ""), d.get("caveats", ""), *src))
        elif kind == "table":
            tid = self._tid(d["table"])
            self._check_col(tid, d.get("date_column"))
            x.execute("INSERT OR REPLACE INTO table_notes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (tid, d.get("grain", ""), d.get("freshness", ""), d.get("soft_delete", ""), d.get("date_column", ""),
                       _j(_l(d.get("recommended_indexes"))), d.get("pitfalls", ""), d.get("summary", ""), *src))
        x.commit()

    def import_path(self, path: str | Path, default_verified=False) -> dict:
        p = Path(path)
        files = sorted(f for f in ([p] if p.is_file() else p.rglob("*")) if f.suffix.lower() in (".yaml", ".yml", ".json"))
        counts = {k: 0 for k in ("concepts", "relations", "columns", "metrics", "tables")}
        for f in files:
            text = f.read_text(encoding="utf-8")
            if f.suffix.lower() == ".json":
                raw = json.loads(text)
            else:
                import yaml
                raw = yaml.safe_load(text) or {}
            src, ver = raw.get("source") or str(f), raw.get("verified", default_verified)
            for key, kind in (("concepts", "concept"), ("relations", "relation"), ("columns", "column"),
                              ("metrics", "metric"), ("tables", "table")):
                for item in raw.get(key) or []:
                    self.upsert(kind, item, src, ver)
                    counts[key] += 1
        self.reindex()
        return counts

    # --- reads -------------------------------------------------------------------
    def rows(self, table: str, where: str = "", args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(f"SELECT * FROM {table} {where}", args)]

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
                           "source": l["source"], "verified": bool(l["verified"])}
                          for l in self.rows("concept_links", "WHERE concept=?", (c["name"],))]
            c["verified"] = bool(c["verified"])
            out["concepts"].append(c)
        for r in self.rows("concept_relations", "ORDER BY from_concept,rel,to_concept"):
            out["relations"].append({"from": r["from_concept"], "rel": r["rel"], "to": r["to_concept"], "edge": r["edge_id"],
                                     "note": r["note"], "source": r["source"], "verified": bool(r["verified"])})
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
        for n in graph.nodes.values():
            rows.append(("table", n["id"], n["id"], normalize(n["id"])))
            for c in n["columns"]:
                rows.append(("column", f"{n['id']}.{c['name']}", c["name"], normalize(n["table"] + " " + c["name"])))
        self.db.executemany("INSERT INTO search_fts(kind,ref,title,body) VALUES(?,?,?,?)", rows)
        self.db.commit()

    def reindex(self) -> None:
        self.db.execute("DELETE FROM search_fts WHERE kind NOT IN ('table','column')")
        rows = []
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
        toks = tokens(query)
        if not toks:
            return []
        q = " OR ".join(f'"{t}"*' for t in toks)
        try:
            cur = self.db.execute(
                "SELECT kind, ref, bm25(search_fts, 0.0, 0.0, 3.0, 1.0) AS s FROM search_fts WHERE search_fts MATCH ? "
                "ORDER BY s LIMIT ?", (q, limit))
        except sqlite3.OperationalError:
            return []
        return [(r["kind"], r["ref"], -r["s"]) for r in cur]
