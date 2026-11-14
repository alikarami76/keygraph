"""Replay: rebuild the meaning layer from saved answers with no model and no database access.

`snapshot_base` keeps the deterministic graph.json as index/base_graph.json (index start calls it). `replay` clears every
llm fact, re-applies index/answers/*.json in unit-id order and renders. `finish` is replay plus index/summary.json
(counts only). Same base graph plus same answers gives byte-identical graph.json, pages, report and explorer."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from keygraph.context import ContextDB
from keygraph.graph import BUILD_FILE, Graph
from keygraph.index.store import LLM, answers_dir, clear_llm, store_unit, strip_llm

BASE = "base_graph.json"


def base_path(out_dir) -> Path:
    return Path(out_dir) / "index" / BASE


def snapshot_base(out_dir) -> Path:
    """Copy the deterministic graph.json to index/base_graph.json (llm edges, if the file holds any, are removed)."""
    out = Path(out_dir)
    src = out / "graph.json"
    if not src.exists():
        raise FileNotFoundError(f"no map in {out}: build one first (keygraph build, scan or demo)")
    dst = base_path(out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if '"llm"' in src.read_text(encoding="utf-8"):
        g = Graph.load(src)
        strip_llm(g)
        g.refresh_stats()
        g.save(dst)
    else:
        shutil.copyfile(src, dst)
    return dst


def schema_fingerprint(data: dict) -> str:
    """A hash of the schema in a graph: the sorted table ids and column ids (edges, notes and the llm layer do not count)."""
    ids = sorted({n["id"] for n in data.get("nodes", [])} |
                 {f"{n['id']}.{c['name']}" for n in data.get("nodes", []) for c in n.get("columns", [])})
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


REBUILT = ("the map was rebuilt since index start; run `keygraph index refresh` (re-snapshots and re-plans) then finish")


def check_fresh(out: Path) -> None:
    """Refuse when graph.json no longer has the schema that index/base_graph.json was snapshotted from (a load, scan or
    build ran since `index start`): replaying would silently revert the new tables and columns."""
    gp = out / "graph.json"
    bp = base_path(out)
    if not (gp.exists() and bp.exists()):
        return
    if schema_fingerprint(json.loads(gp.read_text(encoding="utf-8"))) != schema_fingerprint(json.loads(bp.read_text(encoding="utf-8"))):
        raise ValueError(REBUILT)


def load_base(out: Path) -> Graph:
    bp = base_path(out)
    if not bp.exists():
        raise FileNotFoundError(f"{bp} is missing: run `keygraph index start --dir {out}` first")
    g = Graph(json.loads(bp.read_text(encoding="utf-8")))
    strip_llm(g)
    b = out / BUILD_FILE
    if b.exists():
        try:
            info = json.loads(b.read_text(encoding="utf-8"))
            g.build = info if isinstance(info, dict) else None
        except (OSError, ValueError):
            g.build = None
    return g


def _facts(ctx) -> dict:
    prov = ctx.db.execute("SELECT kind, COUNT(*) n, SUM(verified) v FROM fact_provenance GROUP BY kind ORDER BY kind").fetchall()
    rel = ctx.db.execute("SELECT status, COUNT(*) FROM index_relations GROUP BY status ORDER BY status").fetchall()
    return {"facts": {r["kind"]: r["n"] for r in prov}, "verified_facts": sum(r["v"] or 0 for r in prov),
            "relations": {r[0]: r[1] for r in rel}}


def _crashed(ctx, f: Path, a: dict) -> bool:
    """True for a file written by a submit that never committed: its unit row (same job) is still pending with fewer attempts
    than the file records. Files without the `attempts` field, or from another job, always count."""
    if not isinstance(a.get("attempts"), int):
        return False
    try:
        row = ctx.db.execute("SELECT status, attempts FROM index_units WHERE job_id=? AND unit_id=?",
                             (a.get("job_id"), a.get("unit_id") or f.stem)).fetchone()
    except Exception:  # noqa: BLE001  (a context file without the index tables)
        return False
    return bool(row and row["status"] == "pending" and row["attempts"] < a["attempts"])


def replay(out_dir) -> dict:
    """Rebuild the llm layer from index/answers/ and render. Never opens a database connection to a source."""
    from keygraph.cli import render
    out = Path(out_dir)
    g = load_base(out)
    check_fresh(out)
    files = sorted(answers_dir(out).glob("*.json"), key=lambda p: p.stem)
    have_db = (out / "keygraph.db").exists()
    used = 0
    totals = {"kept": 0, "filled": 0, "probe_failed": 0}
    with ContextDB(out / "keygraph.db", g) as ctx:
        clear_llm(ctx)
        for f in files:
            a = json.loads(f.read_text(encoding="utf-8"))
            if have_db and _crashed(ctx, f, a):
                continue
            used += 1
            unit = {"unit_id": a.get("unit_id") or f.stem, "tables": a.get("tables") or []}
            res = store_unit(ctx, g, a.get("job_id", ""), unit, a.get("answer") or {}, a.get("model", ""), a.get("prompt_hash", ""),
                             a.get("verdicts") or [], reindex=False)
            for k in totals:
                totals[k] += res.get(k, 0)
        ctx.reindex()
        prof = ctx.load_profiles()
        if prof:
            g.data["profiles"] = prof
        render(out, g, ctx)
        facts = _facts(ctx)
    return {"units": used, **totals, **facts,
            "llm_edges": sum(1 for e in g.data["edges"] if e.get("source") == LLM)}


def finish(out_dir) -> dict:
    """replay plus index/summary.json (counts only, no timestamps)."""
    res = replay(out_dir)
    p = Path(out_dir) / "index" / "summary.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return res
