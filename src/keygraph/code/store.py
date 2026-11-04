"""Code index tables in keygraph.db: schema, incremental writes, and the where / what / stats queries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .refs import KIND_ORDER, KINDS, CodeRef, Unresolved

SCHEMA = """
CREATE TABLE IF NOT EXISTS code_repos(name TEXT PRIMARY KEY, root_hint TEXT, indexed_at TEXT, files INT, refs INT,
  fingerprint TEXT);
CREATE TABLE IF NOT EXISTS code_files(repo TEXT, path TEXT, sha1 TEXT, lang TEXT, decl_lines TEXT,
  PRIMARY KEY (repo, path));
CREATE TABLE IF NOT EXISTS code_refs(repo TEXT, path TEXT, line INT, kind TEXT, table_id TEXT, column_name TEXT,
  symbol TEXT, rule TEXT, confidence REAL, snippet TEXT);
CREATE INDEX IF NOT EXISTS code_refs_table ON code_refs(table_id, column_name);
CREATE INDEX IF NOT EXISTS code_refs_path ON code_refs(repo, path, line);
CREATE INDEX IF NOT EXISTS code_refs_symbol ON code_refs(symbol);
CREATE TABLE IF NOT EXISTS code_unresolved(repo TEXT, path TEXT, line INT, kind TEXT, name TEXT, rule TEXT);
"""
TABLES = ("code_repos", "code_files", "code_refs", "code_unresolved")
KIND_SQL = "CASE kind " + " ".join(f"WHEN '{k}' THEN {i}" for k, i in KIND_ORDER.items()) + " END"
REF_ORDER = f"{KIND_SQL}, confidence DESC, repo, path, line, table_id, IFNULL(column_name, ''), IFNULL(symbol, ''), rule"


def ref_dict(r: sqlite3.Row) -> dict:
    return {"repo": r["repo"], "path": r["path"], "line": r["line"], "kind": r["kind"], "table": r["table_id"],
            "column": r["column_name"], "symbol": r["symbol"], "rule": r["rule"], "confidence": r["confidence"],
            "snippet": r["snippet"]}


class CodeStore:
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

    def __enter__(self) -> "CodeStore":
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self.close()
        else:
            self.db.close()

    # --- writes ------------------------------------------------------------------------------------------
    def repo_fingerprint(self, repo: str) -> str | None:
        r = self.db.execute("SELECT fingerprint FROM code_repos WHERE name=?", (repo,)).fetchone()
        return r[0] if r else None

    def drop_repo(self, repo: str) -> None:
        for t in ("code_files", "code_refs", "code_unresolved"):
            self.db.execute(f"DELETE FROM {t} WHERE repo=?", (repo,))
        self.db.execute("DELETE FROM code_repos WHERE name=?", (repo,))

    def file_hashes(self, repo: str) -> dict[str, str]:
        return {r["path"]: r["sha1"] for r in self.db.execute("SELECT path, sha1 FROM code_files WHERE repo=?", (repo,))}

    def decl_lines(self, repo: str) -> dict[str, set[int]]:
        return {r["path"]: set(json.loads(r["decl_lines"] or "[]"))
                for r in self.db.execute("SELECT path, decl_lines FROM code_files WHERE repo=?", (repo,))}

    def delete_file(self, repo: str, path: str) -> None:
        for t in ("code_files", "code_refs", "code_unresolved"):
            self.db.execute(f"DELETE FROM {t} WHERE repo=? AND path=?", (repo, path))

    def replace_file(self, repo: str, path: str, sha1: str, lang: str, decl_lines: list[int], refs: list[CodeRef],
                     unresolved: list[Unresolved]) -> None:
        """Store one file's extraction (everything except uses, which the second pass rewrites per repo)."""
        self.delete_file(repo, path)
        self.db.execute("INSERT INTO code_files VALUES(?,?,?,?,?)", (repo, path, sha1, lang, json.dumps(decl_lines)))
        self.add_refs(refs)
        self.db.executemany("INSERT INTO code_unresolved VALUES(?,?,?,?,?,?)",
                            [(u.repo, u.path, u.line, u.kind, u.name, u.rule) for u in sorted(unresolved, key=Unresolved.sort_key)])

    def add_refs(self, refs: list[CodeRef]) -> None:
        self.db.executemany("INSERT INTO code_refs VALUES(?,?,?,?,?,?,?,?,?,?)",
                            [(r.repo, r.path, r.line, r.kind, r.table_id, r.column, r.symbol, r.rule, r.confidence, r.snippet)
                             for r in sorted(refs, key=CodeRef.sort_key)])

    def delete_uses(self, repo: str) -> None:
        self.db.execute("DELETE FROM code_refs WHERE repo=? AND kind='use' AND rule LIKE 'use:%'", (repo,))

    def declarations(self, repo: str) -> list[dict]:
        return [{"table": r["table_id"], "column": r["column_name"], "symbol": r["symbol"], "kind": r["kind"]}
                for r in self.db.execute("SELECT table_id, column_name, symbol, kind FROM code_refs WHERE repo=? AND kind IN "
                                         "('declare_table','declare_column') ORDER BY table_id, column_name, symbol", (repo,))]

    def file_tables(self, repo: str) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for r in self.db.execute("SELECT DISTINCT path, table_id FROM code_refs WHERE repo=? AND kind!='use'", (repo,)):
            out.setdefault(r["path"], set()).add(r["table_id"])
        return out

    def finish_repo(self, repo: str, root_hint: str, indexed_at: str, fingerprint: str) -> dict:
        files = self.db.execute("SELECT COUNT(*) FROM code_files WHERE repo=?", (repo,)).fetchone()[0]
        refs = self.db.execute("SELECT COUNT(*) FROM code_refs WHERE repo=?", (repo,)).fetchone()[0]
        self.db.execute("INSERT OR REPLACE INTO code_repos VALUES(?,?,?,?,?,?)", (repo, root_hint, indexed_at, files, refs, fingerprint))
        self.db.commit()
        return {"files": files, "refs": refs}

    # --- reads -------------------------------------------------------------------------------------------
    def where(self, table_id: str, column: str | None = None, kind: str | None = None, repo: str | None = None,
              min_confidence: float = 0.0, limit: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM code_refs WHERE table_id=? AND confidence>=?", [table_id, min_confidence]
        if column:
            q, args = q + " AND column_name=?", args + [column]
        if kind:
            q, args = q + " AND kind=?", args + [kind]
        if repo:
            q, args = q + " AND repo=?", args + [repo]
        q += f" ORDER BY {REF_ORDER}" + (f" LIMIT {int(limit)}" if limit else "")
        return [ref_dict(r) for r in self.db.execute(q, args)]

    def counts(self, table_id: str, column: str | None = None) -> dict[str, int]:
        q, args = "SELECT kind, COUNT(*) FROM code_refs WHERE table_id=?", [table_id]
        if column:
            q, args = q + " AND column_name=?", args + [column]
        got = dict(self.db.execute(q + " GROUP BY kind", args).fetchall())
        return {k: got.get(k, 0) for k in KINDS}

    def files_matching(self, path: str, repo: str | None = None) -> list[tuple[str, str]]:
        """(repo, path) pairs for an exact path, else for paths ending in /path."""
        base = "SELECT repo, path FROM code_files WHERE " + ("repo=? AND " if repo else "")
        args = [repo] if repo else []
        rows = self.db.execute(base + "path=? ORDER BY repo, path", args + [path]).fetchall()
        if not rows:
            rows = self.db.execute(base + "path LIKE ? ESCAPE '\\' ORDER BY repo, path",
                                   args + ["%/" + path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")]).fetchall()
        return [(r["repo"], r["path"]) for r in rows]

    def file_refs(self, repo: str, path: str, line: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM code_refs WHERE repo=? AND path=?", [repo, path]
        if line is not None:
            q, args = q + " AND line=?", args + [line]
        return [ref_dict(r) for r in self.db.execute(q + " ORDER BY line, " + REF_ORDER, args)]

    def symbol_refs(self, symbol: str, repo: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM code_refs WHERE symbol=?", [symbol]
        if repo:
            q, args = q + " AND repo=?", args + [repo]
        return [ref_dict(r) for r in self.db.execute(q + " ORDER BY " + REF_ORDER, args)]

    def unresolved(self, repo: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM code_unresolved", []
        if repo:
            q, args = q + " WHERE repo=?", [repo]
        return [dict(r) for r in self.db.execute(q + " ORDER BY repo, path, line, kind, name, rule", args)]

    def stats(self) -> dict:
        out = []
        for r in self.db.execute("SELECT * FROM code_repos ORDER BY name").fetchall():
            name = r["name"]
            by_kind = dict(self.db.execute("SELECT kind, COUNT(*) FROM code_refs WHERE repo=? GROUP BY kind", (name,)).fetchall())
            top = self.db.execute("SELECT table_id, COUNT(*) AS n FROM code_refs WHERE repo=? GROUP BY table_id "
                                  "ORDER BY n DESC, table_id LIMIT 10", (name,)).fetchall()
            unres = self.db.execute("SELECT COUNT(*) FROM code_unresolved WHERE repo=?", (name,)).fetchone()[0]
            out.append({"repo": name, "root": r["root_hint"], "indexed_at": r["indexed_at"], "files": r["files"],
                        "refs": r["refs"], "refs_by_kind": {k: by_kind.get(k, 0) for k in KINDS}, "unresolved": unres,
                        "top_tables": [{"table": t["table_id"], "refs": t["n"]} for t in top]})
        return {"repos": out}

    def summary(self, snippets: bool = False, per_table: int = 50, per_column: int = 20) -> dict:
        """Refs aggregated per table and column for the explorer and the docs: counts by kind and the first refs
        (declarations first). {table_id: {"n": {kind: count}, "refs": [...], "columns": {col: {"n", "refs"}}}}."""
        out: dict = {}
        for r in self.db.execute(f"SELECT * FROM code_refs ORDER BY table_id, {REF_ORDER}"):
            t = out.setdefault(r["table_id"], {"n": {k: 0 for k in KINDS}, "refs": [], "columns": {}})
            d = ref_dict(r)
            if not snippets:
                d.pop("snippet")
            t["n"][r["kind"]] += 1
            if len(t["refs"]) < per_table:
                t["refs"].append(d)
            if r["column_name"]:
                c = t["columns"].setdefault(r["column_name"], {"n": {k: 0 for k in KINDS}, "refs": []})
                c["n"][r["kind"]] += 1
                if len(c["refs"]) < per_column:
                    c["refs"].append(d)
        return out
