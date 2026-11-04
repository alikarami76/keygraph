"""Relations seen in code: equality joins found in source code (code_joins) and in logged queries (query_runs) become
evidence on the graph. An edge between the same two columns gets more confidence; otherwise a new edge is added.
The merge is idempotent: every render first removes what an earlier render merged."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .code.sql import DIALECTS, sql_joins
from .code.store import CodeStore
from .code.vocab import Vocab
from .graph import Graph, edge_id

CODE_RX = re.compile(r"^joined in code \d+ times")
QUERY_RX = re.compile(r"^joined in \d+ logged quer(?:y|ies)")
BOOST = 0.95
NEW_CODE, NEW_QUERIES, NEW_UNKEYED = 0.9, 0.85, 0.8


def _strip(g: Graph) -> None:
    """Undo an earlier merge: drop merged edges, merged evidence and boosted confidences."""
    g.data["edges"][:] = [e for e in g.data["edges"] if e.get("source") not in ("code", "queries")]
    for e in g.data["edges"]:
        if "code_boost_from" in e:
            e["confidence"] = e.pop("code_boost_from")
        e["evidence"] = [x for x in e.get("evidence", []) if not (CODE_RX.match(x) or QUERY_RX.match(x))]


def _query_joins(db_path: Path, vocab: Vocab) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    if not db_path.exists():
        return out
    con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT sql, source FROM query_runs WHERE ok=1 ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return out
    finally:
        con.close()
    cache: dict[tuple, list] = {}
    for sql, source in rows:
        key = (sql, (source or "").lower())
        if key not in cache:
            cache[key] = sql_joins(sql, vocab, source.lower() if (source or "").lower() in DIALECTS else None)
        for pair in cache[key]:
            out[pair] = out.get(pair, 0) + 1
    return out


def _cardinality(node: dict, col: str) -> str:
    if node["primary_key"] == [col] or any(u["columns"] == [col] for u in node.get("unique_keys", [])):
        return "one-to-one"
    return "many-to-one"


def merge_code_joins(g: Graph, db_path: str | Path) -> dict:
    """Merge joins seen in code (and logged queries) from keygraph.db into g.data["edges"]. Returns
    {"boosted": n, "added": n}. Needs sqlglot at index time only; an index without joins merges nothing."""
    db_path = Path(db_path)
    _strip(g)
    code: dict[tuple, list] = {}
    with CodeStore(db_path, readonly=True) as st:
        for repo, path, line, at, ac, bt, bc in st.joins():
            code.setdefault((at, ac, bt, bc), []).append((repo, path, line))
    queries = _query_joins(db_path, Vocab.from_graph(g.data))
    existing: dict[frozenset, dict] = {}
    for e in g.data["edges"]:
        if len(e["from_columns"]) == 1 and len(e["to_columns"]) == 1:
            existing.setdefault(frozenset([(e["from"], e["from_columns"][0]), (e["to"], e["to_columns"][0])]), e)
    boosted = added = 0
    for pair in sorted(set(code) | set(queries)):
        at, ac, bt, bc = pair
        a, b = g.nodes.get(at), g.nodes.get(bt)
        if not a or not b or a.get("mirror_of") or b.get("mirror_of"):
            continue
        ev = []
        if pair in code:
            n = len(code[pair])
            r, p, ln = min(code[pair])
            ev.append(f"joined in code {n} times, e.g. {r}:{p}:{ln}")
        if pair in queries:
            n = queries[pair]
            ev.append(f"joined in {n} logged quer{'y' if n == 1 else 'ies'}")
        e = existing.get(frozenset([(at, ac), (bt, bc)]))
        if e is not None:
            e["code_boost_from"] = e["confidence"]
            e["confidence"] = max(e["confidence"], BOOST)
            e["evidence"] = list(e.get("evidence", [])) + ev
            boosted += 1
            continue
        pk_a, pk_b = a["primary_key"] == [ac], b["primary_key"] == [bc]
        conf = NEW_CODE if pair in code else NEW_QUERIES
        if pk_a != pk_b:
            frm, to = ((bt, bc), (at, ac)) if pk_a else ((at, ac), (bt, bc))
        else:
            frm, to, conf = (at, ac), (bt, bc), NEW_UNKEYED
        new = {"kind": "inferred", "status": "inferred", "from": frm[0], "from_columns": [frm[1]], "to": to[0],
               "to_columns": [to[1]], "confidence": conf, "cardinality": _cardinality(g.nodes[frm[0]], frm[1]),
               "ambiguous": False, "evidence": ev, "source": "code" if pair in code else "queries"}
        new["id"] = edge_id(new)
        g.data["edges"].append(new)
        added += 1
    g.data["edges"].sort(key=lambda e: e["id"])
    g.refresh_stats()
    return {"boosted": boosted, "added": added}
