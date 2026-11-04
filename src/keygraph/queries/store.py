"""Query log tables in keygraph.db: runs, fingerprints, links to tables and columns, and the reads behind the
queries commands."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..context import normalize, tokens
from ..code.refs import mask_secrets
from ..code.vocab import Vocab
from .fingerprint import fingerprint, mask_sql
from .link import link
from .timing import timing

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_fingerprints(fp TEXT PRIMARY KEY, normalized_sql TEXT, first_seen TEXT, last_seen TEXT,
  runs INT, people INT, context TEXT);
CREATE TABLE IF NOT EXISTS query_runs(id INTEGER PRIMARY KEY, fp TEXT, sql TEXT, source TEXT, ms REAL, rows INT, at TEXT,
  by TEXT, context TEXT, tags TEXT, ok INT, error TEXT, unresolved TEXT);
CREATE TABLE IF NOT EXISTS query_refs(fp TEXT, table_id TEXT, column_name TEXT, role TEXT);
CREATE INDEX IF NOT EXISTS query_refs_table ON query_refs(table_id, column_name);
CREATE INDEX IF NOT EXISTS query_runs_fp ON query_runs(fp, at);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(kind, ref, title, body, tokenize='unicode61');
"""
TABLES = ("query_fingerprints", "query_runs", "query_refs")
TOP_ORDER = {"runs": "runs DESC, last_seen DESC, fp", "recent": "last_seen DESC, runs DESC, fp",
             "people": "people DESC, runs DESC, fp"}
SQL_SHOWN = 300


def _masked(text: str | None) -> str:
    """Free text with secret-looking values masked line by line (errors can echo connection strings)."""
    return "\n".join(mask_secrets(line) for line in (text or "").strip().splitlines())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class QueryStore:
    def __init__(self, path: str | Path, readonly: bool = False):
        """readonly: open an existing file without writing; missing file or tables read as empty."""
        self.path = Path(path)
        if readonly and self.path.exists():
            self.db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
            have = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master")}
            if not set(TABLES) <= have:
                self.db.close()
                self.db = sqlite3.connect(":memory:")
                self.db.executescript(SCHEMA)
        elif readonly:
            self.db = sqlite3.connect(":memory:")
            self.db.executescript(SCHEMA)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path)
            self.db.executescript(SCHEMA)
        self.db.row_factory = sqlite3.Row

    def close(self) -> None:
        try:
            self.db.commit()
        finally:
            self.db.close()

    def __enter__(self) -> "QueryStore":
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self.close()
        else:
            self.db.close()

    # --- writes ------------------------------------------------------------------------------------------
    def add(self, sql: str, source: str, ms: float | None = None, rows: int | None = None, context: str = "",
            tags=(), by: str = "", at: str | None = None, error: str | None = None, keep_literals: bool = False,
            vocab: Vocab | None = None, dialect: str | None = None) -> dict:
        if not str(sql or "").strip():
            raise ValueError("empty SQL")
        if not str(source or "").strip():
            raise ValueError("a query needs a source (the database or tool it ran on)")
        fp, norm = fingerprint(sql)
        norm = _masked(norm)  # double-quoted MySQL strings survive normalisation; never store their secrets
        refs, unresolved = link(sql, vocab, dialect)
        tags = sorted({str(t).strip() for t in (tags.split(",") if isinstance(tags, str) else tags) if str(t).strip()})
        at = at or _now()
        cur = self.db.execute(
            "INSERT INTO query_runs(fp, sql, source, ms, rows, at, by, context, tags, ok, error, unresolved) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (fp, mask_sql(sql, keep_literals), source.strip(), None if ms is None else float(ms),
             None if rows is None else int(rows), at, (by or "").strip(), _masked(context), json.dumps(tags),
             0 if error else 1, _masked(error) or None, json.dumps(unresolved)))
        self.db.execute("DELETE FROM query_refs WHERE fp=?", (fp,))
        self.db.executemany("INSERT INTO query_refs VALUES(?,?,?,?)", [(fp, t, c or None, r) for t, c, r in refs])
        self._refresh(fp, norm)
        self.db.commit()
        return {"fp": fp, "run": cur.lastrowid, "normalized_sql": norm,
                "tables": sorted({t for t, _, _ in refs}), "unresolved": unresolved}

    def _refresh(self, fp: str, norm: str) -> None:
        agg = self.db.execute("SELECT COUNT(*) AS runs, COUNT(DISTINCT NULLIF(by, '')) AS people, MIN(at) AS first, "
                              "MAX(at) AS last FROM query_runs WHERE fp=?", (fp,)).fetchone()
        ctx = self.db.execute("SELECT context FROM query_runs WHERE fp=? AND context!='' ORDER BY at DESC, id DESC LIMIT 1",
                              (fp,)).fetchone()
        context = ctx[0] if ctx else ""
        self.db.execute("INSERT OR REPLACE INTO query_fingerprints VALUES(?,?,?,?,?,?,?)",
                        (fp, norm, agg["first"], agg["last"], agg["runs"], agg["people"], context))
        self.db.execute("DELETE FROM search_fts WHERE kind='query' AND ref=?", (fp,))
        self.db.execute("INSERT INTO search_fts(kind, ref, title, body) VALUES('query', ?, ?, ?)",
                        (fp, fp, normalize(context + " " + norm)))

    # --- reads -------------------------------------------------------------------------------------------
    def _fp_dict(self, r: sqlite3.Row, with_timing: bool = True) -> dict:
        d = {"fp": r["fp"], "context": r["context"], "normalized_sql": r["normalized_sql"], "runs": r["runs"],
             "people": r["people"], "first_seen": r["first_seen"], "last_seen": r["last_seen"]}
        d["tables"] = [x[0] for x in self.db.execute("SELECT DISTINCT table_id FROM query_refs WHERE fp=? ORDER BY table_id", (r["fp"],))]
        if with_timing:
            ms = [x[0] for x in self.db.execute("SELECT ms FROM query_runs WHERE fp=? AND ms IS NOT NULL ORDER BY at, id", (r["fp"],))]
            d.update(timing(ms))
        return d

    def top(self, by: str = "runs", table_id: str | None = None, limit: int = 10) -> list[dict]:
        if by not in TOP_ORDER:
            raise ValueError(f"--by must be one of {', '.join(TOP_ORDER)}")
        q, args = "SELECT * FROM query_fingerprints", []
        if table_id:
            q, args = q + " WHERE fp IN (SELECT fp FROM query_refs WHERE table_id=?)", [table_id]
        return [self._fp_dict(r) for r in self.db.execute(f"{q} ORDER BY {TOP_ORDER[by]} LIMIT ?", args + [int(limit)]).fetchall()]

    def slow(self, limit: int = 10, min_runs: int = 1) -> list[dict]:
        rows = [self._fp_dict(r) for r in self.db.execute("SELECT * FROM query_fingerprints WHERE runs>=? ORDER BY fp",
                                                           (int(min_runs),)).fetchall()]
        rows = [r for r in rows if r["median_ms"] is not None]
        return sorted(rows, key=lambda r: (-r["median_ms"], -(r["p95_ms"] or 0), r["fp"]))[:limit]

    def for_ref(self, table_id: str, column: str | None = None) -> list[dict]:
        q, args = "SELECT fp, role FROM query_refs WHERE table_id=?", [table_id]
        if column:
            q, args = q + " AND column_name=?", args + [column]
        roles: dict[str, set[str]] = {}
        for r in self.db.execute(q, args):
            roles.setdefault(r["fp"], set()).add(r["role"])
        out = []
        for fp in roles:
            r = self.db.execute("SELECT * FROM query_fingerprints WHERE fp=?", (fp,)).fetchone()
            if r:
                out.append({**self._fp_dict(r), "roles": sorted(roles[fp])})
        return sorted(out, key=lambda d: (-d["runs"], d["fp"]))

    def search(self, text: str, limit: int = 10) -> list[dict]:
        words = [t for t in tokens(text) if len(t) >= 2]
        if not words:
            return []
        match = " OR ".join(f'"{w}"*' for w in words)
        try:
            hits = self.db.execute("SELECT ref, bm25(search_fts, 0.0, 0.0, 1.0, 1.0) AS s FROM search_fts WHERE search_fts "
                                   "MATCH ? AND kind='query' ORDER BY s, ref LIMIT ?", (match, int(limit))).fetchall()
        except sqlite3.OperationalError:
            return []
        out = []
        for h in hits:
            r = self.db.execute("SELECT * FROM query_fingerprints WHERE fp=?", (h["ref"],)).fetchone()
            if r:
                out.append({**self._fp_dict(r), "score": round(-h["s"], 3)})
        return out

    def show(self, fp: str) -> dict:
        rows = self.db.execute("SELECT * FROM query_fingerprints WHERE fp LIKE ? ORDER BY fp", (fp + "%",)).fetchall()
        if not rows:
            raise ValueError(f"no query fingerprint starts with '{fp}'")
        if len(rows) > 1:
            raise ValueError(f"'{fp}' matches {len(rows)} fingerprints: {', '.join(r['fp'] for r in rows[:5])}")
        d = self._fp_dict(rows[0])
        d["refs"] = [{"table": r["table_id"], "column": r["column_name"], "role": r["role"]}
                     for r in self.db.execute("SELECT * FROM query_refs WHERE fp=? ORDER BY table_id, IFNULL(column_name, ''), role",
                                              (d["fp"],))]
        d["runs_list"] = [{"id": r["id"], "at": r["at"], "source": r["source"], "ms": r["ms"], "rows": r["rows"], "by": r["by"],
                           "context": r["context"], "tags": json.loads(r["tags"] or "[]"), "ok": bool(r["ok"]), "error": r["error"],
                           "unresolved": json.loads(r["unresolved"] or "[]"), "sql": r["sql"]}
                          for r in self.db.execute("SELECT * FROM query_runs WHERE fp=? ORDER BY at, id", (d["fp"],))]
        return d

    def stats(self) -> dict:
        one = lambda q: self.db.execute(q).fetchone()[0]  # noqa: E731
        unres: dict[str, int] = {}
        for (u,) in self.db.execute("SELECT unresolved FROM query_runs"):
            for name in json.loads(u or "[]"):
                unres[name] = unres.get(name, 0) + 1
        return {"runs": one("SELECT COUNT(*) FROM query_runs"), "fingerprints": one("SELECT COUNT(*) FROM query_fingerprints"),
                "failed_runs": one("SELECT COUNT(*) FROM query_runs WHERE ok=0"),
                "sources": {r[0]: r[1] for r in self.db.execute("SELECT source, COUNT(*) FROM query_runs GROUP BY source ORDER BY source")},
                "tables": one("SELECT COUNT(DISTINCT table_id) FROM query_refs"),
                "unresolved": [{"name": k, "runs": v} for k, v in sorted(unres.items(), key=lambda kv: (-kv[1], kv[0]))]}

    def summary(self, per_table: int = 10) -> dict:
        """Per table: its top fingerprints by runs, for the explorer and the table pages."""
        out: dict[str, list[dict]] = {}
        for (t,) in self.db.execute("SELECT DISTINCT table_id FROM query_refs ORDER BY table_id").fetchall():
            out[t] = [{"fp": d["fp"], "context": d["context"], "sql": d["normalized_sql"][:SQL_SHOWN], "runs": d["runs"],
                       "median_ms": d["median_ms"], "p95_ms": d["p95_ms"]} for d in self.top("runs", t, per_table)]
        return out
