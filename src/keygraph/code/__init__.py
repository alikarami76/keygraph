"""keygraph code index: link source code to the tables and columns of a built keygraph.

    from keygraph import code
    code.index_repo("keygraph-out/keygraph.db", "./api", "api")
    code.where("keygraph-out/keygraph.db", "shop.orders.status")
    code.what("keygraph-out/keygraph.db", path="app/models.py", line=12)

Deterministic and read-only towards the code: files are read as text, never imported or executed."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .. import __version__, devlog
from ..graph import Graph
from .patterns import PatternError, builtin_rules, load_patterns, run_rules
from .refs import KINDS, CodeRef
from . import sql as _sql
from .sql import sql_join_refs, sql_refs
from .store import CodeStore
from .uses import build_index, find_uses
from .vocab import Vocab
from .walk import Lines, read, walk

__all__ = ["index_repo", "where", "what", "stats", "PatternError", "KINDS"]


def _patterns(db_path: Path, patterns) -> list[Path]:
    paths = [Path(p) for p in patterns or []]
    auto = db_path.parent / "code-patterns.yaml"
    if auto.exists() and auto.resolve() not in {p.resolve() for p in paths}:
        paths.append(auto)
    return paths


def index_repo(db_path: str | Path, root: str | Path, name: str | None = None, patterns=(), exclude=(),
               full: bool = False, max_file_kb: int = 1024, max_uses: int = 500, dialect: str | None = None,
               now: str | None = None, copy_schemas=()) -> dict:
    """Index one repo into keygraph.db. Unchanged files (same sha1, same rules and vocabulary) are not re-read for
    declarations and SQL; the uses pass always runs over the whole repo. Returns counts, unresolved and caps."""
    db_path, root = Path(db_path), Path(root)
    if not root.is_dir():
        raise ValueError(f"repo root {root} is not a directory")
    name = name or root.resolve().name
    vocab = Vocab.load(db_path, copy_schemas)
    rules = builtin_rules() + load_patterns(_patterns(db_path, patterns))
    fp = hashlib.sha1(json.dumps([__version__, vocab.fingerprint(), [r.spec() for r in rules], dialect or "",
                                  _sql.sqlglot is not None, "joins"],
                                 sort_keys=True).encode()).hexdigest()
    t0 = time.time()
    changed = deleted = 0
    with CodeStore(db_path) as st:
        if full or st.repo_fingerprint(name) != fp:
            st.drop_repo(name)
        known = st.file_hashes(name)
        with devlog.phase("code.walk") as ph:
            files = walk(root, tuple(exclude), max_file_kb)
            ph["files"] = len(files)
        t_files = time.perf_counter()
        seen: dict[str, str] = {}
        for f in files:
            got = read(f)
            if got is None:
                continue
            sha, text = got
            seen[f.path] = text
            if known.get(f.path) == sha:
                continue
            L = Lines(text)
            res = run_rules(name, f.path, f.lang, text, rules, vocab, L)
            srefs, sunres, slines = sql_refs(name, f.path, f.lang, text, vocab, dialect, L)
            keyed = {r.key(): r for r in srefs}
            keyed.update({r.key(): r for r in res.refs})  # a declaration wins over SQL on the same line
            st.replace_file(name, f.path, sha, f.lang, sorted(set(res.decl_lines) | set(slines)), list(keyed.values()),
                            res.unresolved + sunres, sql_join_refs(name, f.path, f.lang, text, vocab, dialect, L))
            changed += 1
        for path in sorted(set(known) - set(seen)):
            st.delete_file(name, path)
            deleted += 1
        devlog.trace("phase", name="code.declarations_and_sql", ms=round((time.perf_counter() - t_files) * 1000, 2),
                     read=len(seen), changed=changed, deleted=deleted)
        t_uses = time.perf_counter()
        # second pass: uses over every file, against all declarations of the repo
        st.delete_uses(name)
        idx = build_index(st.declarations(name))
        skip, ftables = st.decl_lines(name), st.file_tables(name)
        per_col: dict[tuple[str, str], int] = {}
        capped: dict[str, int] = {}
        uses: list[CodeRef] = []
        for path in sorted(seen):
            for r in find_uses(name, path, seen[path], idx, skip.get(path, set()), ftables.get(path, set())):
                k = (r.table_id, r.column or "")
                per_col[k] = per_col.get(k, 0) + 1
                if per_col[k] > max_uses:
                    capped[f"{r.table_id}.{r.column}"] = per_col[k]
                    continue
                uses.append(r)
        devlog.trace("phase", name="code.uses", ms=round((time.perf_counter() - t_uses) * 1000, 2), uses=len(uses), capped=len(capped))
        with devlog.phase("code.store", uses=len(uses)):
            st.add_refs(uses)
        counts = st.finish_repo(name, root.resolve().name, now or time.strftime("%Y-%m-%dT%H:%M:%S"), fp)
        unresolved = len(st.unresolved(name))
    return {"repo": name, "files": counts["files"], "changed": changed, "deleted": deleted, "refs": counts["refs"],
            "uses": len(uses), "unresolved": unresolved, "capped": dict(sorted(capped.items())), "max_uses": max_uses,
            "seconds": round(time.time() - t0, 1)}


def _graph(db_path: Path) -> Graph:
    g = db_path.parent / "graph.json"
    if not g.exists():
        raise ValueError(f"no graph at {g}; build the graph first")
    return Graph.load(g)


def where(db_path: str | Path, ref: str, kind: str | None = None, repo: str | None = None,
          min_confidence: float = 0.0, limit: int | None = None, readonly: bool = True) -> dict:
    """Data -> code: every ref to a table (`schema.table` or a unique bare name) or one column (`table.column`),
    declarations first, then writes, reads and uses, each by confidence."""
    db_path = Path(db_path)
    if kind and kind not in KINDS:
        raise ValueError(f"unknown kind '{kind}'; use one of {', '.join(KINDS)}")
    tid, col = _graph(db_path).split_column(ref)
    with CodeStore(db_path, readonly=readonly) as st:
        refs = st.where(tid, col, kind, repo, min_confidence, limit)
        counts = st.counts(tid, col)
    return {"table": tid, "column": col, "counts": counts, "total": sum(counts.values()), "refs": refs}


def what(db_path: str | Path, path: str | None = None, line: int | None = None, symbol: str | None = None,
         repo: str | None = None, readonly: bool = True) -> dict:
    """Code -> data: the refs of a file (or one line of it), or of a symbol. With a line, also the declarations
    of the entity the file declares that precede the line (nearest table declaration at or before it)."""
    db_path = Path(db_path)
    if not path and not symbol:
        raise ValueError("give a path (PATH or PATH:LINE) or a symbol")
    with CodeStore(db_path, readonly=readonly) as st:
        if symbol:
            return {"symbol": symbol, "refs": st.symbol_refs(symbol, repo)}
        files = st.files_matching(path, repo)
        out = []
        for rp, p in files:
            refs = st.file_refs(rp, p, line)
            entity = None
            if line is not None:
                tables = [r for r in st.file_refs(rp, p) if r["kind"] == "declare_table"]
                before = [r for r in tables if r["line"] <= line] or tables[:1]
                if before:
                    t = before[-1]
                    entity = {"table": t["table"], "symbol": t["symbol"], "line": t["line"],
                              "refs": [r for r in st.file_refs(rp, p) if r["table"] == t["table"]
                                       and r["kind"] in ("declare_table", "declare_column")]}
            out.append({"repo": rp, "path": p, "line": line, "refs": refs, "entity": entity})
    return {"files": out}


def stats(db_path: str | Path, readonly: bool = True) -> dict:
    with CodeStore(Path(db_path), readonly=readonly) as st:
        s = st.stats()
        s["unresolved"] = st.unresolved()[:200]
    return s
