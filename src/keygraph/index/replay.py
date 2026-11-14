"""Replay: rebuild the meaning layer from saved answers with no model and no database access.

`snapshot_base` keeps the deterministic graph.json as index/base_graph.json (index start calls it). `replay` clears every
llm fact, re-applies index/answers/*.json in unit-id order and renders. `finish` is replay plus index/summary.json
(counts only). Same base graph plus same answers gives byte-identical graph.json, pages, report and explorer."""

from __future__ import annotations

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


def _load_base(out: Path) -> Graph:
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


def replay(out_dir) -> dict:
    """Rebuild the llm layer from index/answers/ and render. Never opens a database connection to a source."""
    from keygraph.cli import render
    out = Path(out_dir)
    g = _load_base(out)
    files = sorted(answers_dir(out).glob("*.json"), key=lambda p: p.stem)
    totals = {"kept": 0, "filled": 0, "probe_failed": 0}
    with ContextDB(out / "keygraph.db", g) as ctx:
        clear_llm(ctx)
        for f in files:
            a = json.loads(f.read_text(encoding="utf-8"))
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
    return {"units": len(files), **totals, **facts,
            "llm_edges": sum(1 for e in g.data["edges"] if e.get("source") == LLM)}


def finish(out_dir) -> dict:
    """replay plus index/summary.json (counts only, no timestamps)."""
    res = replay(out_dir)
    p = Path(out_dir) / "index" / "summary.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return res
