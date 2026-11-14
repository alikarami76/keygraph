"""Store accepted index answers in keygraph.db, apply LLM relations as graph edges, and save the answers for replay.

Precedence (spec section 8.5): an LLM fact fills empty fields and replaces rows whose source is `llm`; it never changes a
row a human wrote, verified or not. Every fact gets a fact_provenance row. Free text that reaches provenance, relation
reasons or edge evidence passes through the PII scrub and the secret maskers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from keygraph.code.refs import mask_secrets
from keygraph.graph import Graph, edge_id
from keygraph.index.pii import mask_keywords, scrub
from keygraph.index.schema import ITEMS, SCHEMA_VERSION

LLM = "llm"
CAP_UNVERIFIED, CAP_VERIFIED = 0.75, 0.9
ANSWERS = "answers"
VERDICT_KEYS = ("verified", "check", "rate", "n", "error", "skipped")
_PERF_LISTS = ("common_filters", "slow_patterns", "index_friendly_filters", "hints")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def clean(text, limit: int = 400) -> str:
    """Free text as it may be stored: secrets masked, personal values scrubbed, cut to limit."""
    s = str(text if text is not None else "")[: limit * 2]
    return scrub(mask_keywords(mask_secrets(s)))[:limit]


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True) if v not in (None, "") else ""


def _empty(v) -> bool:
    return v in (None, "", "[]", "{}", [], {})


def _clamp(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def accepted_answer(validation: dict, verdicts: list[dict]) -> tuple[dict, list[dict]]:
    """The answer holding only the items `validate` accepted, and the verdicts re-indexed to match it."""
    answer: dict[str, list] = {}
    remap: dict[tuple, int] = {}
    for ent in validation["accepted"]:
        lst = answer.setdefault(ent["kind"], [])
        remap[(ent["kind"], ent["index"])] = len(lst)
        lst.append(ent["item"])
    out = [{**v, "index": remap[(v["kind"], v["index"])]} for v in verdicts if (v["kind"], v["index"]) in remap]
    return answer, out


def _verdict(v: dict | None, keep=VERDICT_KEYS) -> dict:
    """Only the documented verdict keys, strings cleaned: nothing else an agent or a probe returned is stored."""
    return {k: (clean(v[k], 80) if isinstance(v[k], str) else v[k]) for k in sorted(keep) if v and k in v}


def _tid(g: Graph, ref) -> str:
    try:
        return g.resolve(str(ref))
    except ValueError:
        return ""


def _prov_filled(ctx, kind: str, ref: str) -> dict:
    r = ctx.db.execute("SELECT probe FROM fact_provenance WHERE kind=? AND ref=?", (kind, ref)).fetchone()
    try:
        return (json.loads(r[0]) if r and r[0] else {}).get("filled") or {}
    except ValueError:
        return {}


def _prov(ctx, kind: str, ref: str, meta: dict, conf: float, verdict: dict | None, reason: str, filled: dict | None = None) -> None:
    """One fact_provenance row, replacing an earlier row for the same (kind, ref)."""
    probe: dict = {"reason": clean(reason, 240)}
    if verdict:
        probe["verdict"] = _verdict(verdict)
    if filled:
        old = _prov_filled(ctx, kind, ref)
        if old.get("table") == filled["table"] and old.get("keys") == filled["keys"]:
            filled = {**filled, "fields": {**old.get("fields", {}), **filled["fields"]},
                      "old": {**filled["old"], **old.get("old", {})}}
        probe["filled"] = filled
    ok = bool(verdict and verdict.get("verified") is True)
    ctx.db.execute("INSERT OR REPLACE INTO fact_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (kind, ref, LLM, meta["model"], meta["prompt_hash"], meta["job_id"], meta["unit_id"], round(conf, 2),
                    1 if ok else 0, f"probe:{verdict.get('check')}" if ok else "", json.dumps(probe, ensure_ascii=False, sort_keys=True),
                    meta["now"]))


def _write_row(ctx, table: str, keys: dict, new: dict, meta: dict, counts: dict) -> tuple[str, dict]:
    """Write one context row under the precedence rules. Returns (state, filled): new, replaced, filled or kept."""
    cols = [r[1] for r in ctx.db.execute(f"PRAGMA table_info({table})")]
    where = " AND ".join(f"{k}=?" for k in keys)
    old = ctx.db.execute(f"SELECT * FROM {table} WHERE {where}", tuple(keys.values())).fetchone()
    if old is None or old["source"] == LLM:
        row = {**{c: (0 if c in ("verified", "pii") else "") for c in cols}, **keys, **new,
               "source": LLM, "verified": 0, "updated_at": meta["now"]}
        ctx.db.execute(f"INSERT OR REPLACE INTO {table} VALUES({','.join('?' * len(cols))})", [row[c] for c in cols])
        return ("replaced" if old else "new"), {}
    filled, blocked = {}, 0
    prior: dict = {}
    for f, v in new.items():
        if _empty(v):
            continue
        if _empty(old[f]):
            filled[f] = v
            prior[f] = old[f]
        elif old[f] != v:
            blocked += 1
    if filled:
        ctx.db.execute(f"UPDATE {table} SET {', '.join(f'{f}=?' for f in filled)} WHERE {where}", [*filled.values(), *keys.values()])
    counts["kept"] += blocked
    counts["filled"] += len(filled)
    return ("filled" if filled else "kept"), ({"table": table, "keys": keys, "fields": filled, "old": prior} if filled else {})


def _record(ctx, table, keys, new, kind, ref, meta, counts, conf, verdict, reason) -> str:
    state, filled = _write_row(ctx, table, keys, new, meta, counts)
    if state != "kept":
        _prov(ctx, kind, ref, meta, conf, verdict, reason, filled)
        counts[kind] = counts.get(kind, 0) + 1
    return state


def store_unit(ctx, g: Graph, job_id: str, unit: dict, answer: dict, model: str, prompt_hash: str, verdicts: list[dict],
               *, reindex: bool = True) -> dict:
    """Write the accepted items of one unit and a provenance row per fact. `answer` holds accepted items only
    (see accepted_answer); verdicts carry the (kind, index) of an item in that answer. Returns a summary of counts."""
    meta = {"job_id": job_id, "unit_id": unit["unit_id"], "model": clean(model, 120), "prompt_hash": prompt_hash, "now": _now()}
    vd = {(v["kind"], v["index"]): v for v in verdicts}
    counts: dict = {"kept": 0, "filled": 0, "probe_failed": 0}
    perf: dict[str, dict] = {}
    perf_src: dict[str, tuple] = {}

    for i, it in enumerate(answer.get("relations") or []):
        v = vd.get(("relations", i))
        ft, tt = _tid(g, it["from_table"]), _tid(g, it["to_table"])
        if not ft or not tt:
            continue
        fc, tc = list(it["from_columns"]), list(it["to_columns"])
        rel = f"{ft}({','.join(fc)})->{tt}({','.join(tc)})"
        ok, bad = bool(v and v.get("verified") is True), bool(v and v.get("verified") is False)
        reason = clean(it.get("reason", ""), 240)
        ctx.db.execute("INSERT OR REPLACE INTO index_relations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rel, unit["unit_id"], ft, json.dumps(fc), tt, json.dumps(tc), _clamp(it.get("confidence")), reason,
                        "verified" if ok else "rejected" if bad else "unverified",
                        json.dumps(_verdict(v)), meta["model"],
                        job_id, meta["now"], it.get("cardinality", "")))
        _prov(ctx, "relation", rel, meta, CAP_VERIFIED if ok else min(_clamp(it.get("confidence")), CAP_UNVERIFIED), v, reason)
        counts["rejected_relations" if bad else "relations"] = counts.get("rejected_relations" if bad else "relations", 0) + 1

    for i, it in enumerate(answer.get("columns") or []):
        tid = _tid(g, it["table"])
        if not tid:
            continue
        v = vd.get(("columns", i))
        codes = it.get("codes") or {}
        if v and v.get("verified") is False and codes:
            codes = {}
            counts["probe_failed"] += 1
        _record(ctx, "column_semantics", {"table_id": tid, "col": it["column"]},
                {"meaning": clean(it.get("meaning", "")), "unit": clean(it.get("unit", ""), 120),
                 "codes": _j({str(k): clean(x, 120) for k, x in codes.items()}), "null_meaning": clean(it.get("null_meaning", ""), 240)},
                "column", f"{tid}.{it['column']}", meta, counts, _clamp(it.get("confidence")), v, it.get("reason", ""))

    for i, it in enumerate(answer.get("tables") or []):
        tid = _tid(g, it["table"])
        if not tid:
            continue
        v = vd.get(("tables", i))
        grain, filters = it.get("grain", ""), list(it.get("default_filters") or [])
        if v and v.get("verified") is False and (grain or filters):
            grain, filters = "", []
            counts["probe_failed"] += 1
        _record(ctx, "table_notes", {"table_id": tid},
                {"grain": clean(grain, 240), "freshness": clean(it.get("freshness", ""), 240),
                 "soft_delete": clean(it.get("soft_delete", ""), 240),
                 "pitfalls": "; ".join(clean(p, 240) for p in it.get("pitfalls") or []), "summary": clean(it.get("purpose", ""))},
                "table", tid, meta, counts, _clamp(it.get("confidence")), v, it.get("reason", ""))
        if filters:
            cur = perf.setdefault(tid, {}).get("default_filters", [])
            perf[tid]["default_filters"] = cur + [x for x in (clean(f, 240) for f in filters) if x not in cur]
            perf_src.setdefault(tid, (_clamp(it.get("confidence")), v, it.get("reason", "")))

    for i, it in enumerate(answer.get("performance") or []):
        tid = _tid(g, it["table"])
        if not tid:
            continue
        for k in _PERF_LISTS:
            if it.get(k):
                cur = perf.setdefault(tid, {}).get(k, [])
                perf[tid][k] = cur + [x for x in (clean(y, 240) for y in it[k]) if x not in cur]
        perf_src[tid] = (_clamp(it.get("confidence")), vd.get(("performance", i)), it.get("reason", ""))

    for tid in sorted(perf):
        old = ctx.db.execute("SELECT source, notes FROM performance_notes WHERE table_id=?", (tid,)).fetchone()
        if old and old["source"] != LLM:
            counts["kept"] += 1
            continue
        merged = perf[tid]
        if old:  # a resubmit adds to what this unit stored before: union per list, so live and replay agree
            try:
                had = json.loads(old["notes"])
            except ValueError:
                had = {}
            merged = {k: (had.get(k) or []) + [x for x in perf[tid].get(k, []) if x not in (had.get(k) or [])]
                      for k in sorted(set(had) | set(perf[tid]))}
        ctx.db.execute("INSERT OR REPLACE INTO performance_notes VALUES(?,?,?,?)",
                       (tid, json.dumps(merged, ensure_ascii=False, sort_keys=True), LLM, meta["now"]))
        conf, v, reason = perf_src[tid]
        _prov(ctx, "performance", tid, meta, conf, v, reason)
        counts["performance"] = counts.get("performance", 0) + 1

    for it in answer.get("concepts") or []:
        name = clean(it["name"], 120).strip()
        tables = [t for t in (_tid(g, x) for x in it.get("tables") or []) if t]
        _record(ctx, "concepts", {"name": name},
                        {"definition": clean(it.get("definition", "")), "synonyms": _j([clean(s, 120) for s in it.get("synonyms") or []])},
                        "concept", name, meta, counts, _clamp(it.get("confidence")), None, it.get("reason", ""))
        ctx.db.execute("DELETE FROM concept_links WHERE concept=? AND source=?", (name, LLM))
        for t in sorted(set(tables)):
            if not ctx.db.execute("SELECT 1 FROM concept_links WHERE concept=? AND table_id=? AND col='' AND role='primary-record'",
                                  (name, t)).fetchone():
                ctx.db.execute("INSERT INTO concept_links VALUES(?,?,?,?,?,?,?,?)",
                               (name, t, "", "primary-record", "", LLM, 0, meta["now"]))

    for it in answer.get("metrics") or []:
        tbls = sorted({t for t in (_tid(g, x) for x in it.get("tables") or []) if t})
        _record(ctx, "metrics", {"name": clean(it["name"], 120)},
                {"definition": clean(it.get("definition", "")), "expression": clean(it.get("expression", "")),
                 "grain": clean(it.get("grain", ""), 240), "tables": _j(tbls), "filters": clean(it.get("filters", ""), 240),
                 "caveats": clean(it.get("caveats", ""), 240)},
                "metric", clean(it["name"], 120), meta, counts, _clamp(it.get("confidence")), None, it.get("reason", ""))

    ctx.db.commit()
    if reindex:
        ctx.reindex()
    return counts


def answers_dir(out_dir) -> Path:
    return Path(out_dir) / "index" / ANSWERS


def save_answer(out_dir, unit: dict, job_id: str, answer: dict, model: str, prompt_hash: str, verdicts: list[dict],
                attempts: int | None = None) -> Path:
    """index/answers/<unit_id>.json: sorted keys, no timestamps. Replay reads these files."""
    p = answers_dir(out_dir) / f"{unit['unit_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    known = {k: [{f: x for f, x in it.items() if f in ITEMS[k]["properties"]} for it in items]
             for k, items in answer.items() if k in ITEMS}
    body = {"answer": known, "job_id": job_id, "model": clean(model, 120), "prompt_hash": prompt_hash,
            "schema_version": SCHEMA_VERSION, "tables": sorted(unit.get("tables") or []), "unit_id": unit["unit_id"],
            "verdicts": [{"kind": v.get("kind"), "index": v.get("index"), **_verdict(v)} for v in verdicts]}
    if attempts is not None:  # the unit's attempts after this submit: replay uses it to tell a file from a crashed submit
        body["attempts"] = attempts
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)  # atomic: replay sees the old file or the new one, never half of one
    return p


def set_saved_attempts(out_dir, unit_id: str, attempts: int) -> None:
    """Rewrite the `attempts` of a saved answer (refresh puts the unit back to 0); no file means nothing to do."""
    p = answers_dir(out_dir) / f"{unit_id}.json"
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(body, dict) and "attempts" in body:
        body["attempts"] = attempts
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, p)


def merge_saved(out_dir, unit: dict, answer: dict, verdicts: list[dict]) -> tuple[dict, list[dict]]:
    """The saved answer of a unit united with newly accepted items (a resubmit of rejected items). An item already saved is
    not added twice. Verdicts of both are re-indexed to the united answer."""
    p = answers_dir(out_dir) / f"{unit['unit_id']}.json"
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
        have = {k: list(v) for k, v in (old.get("answer") or {}).items() if k in ITEMS}
        old_v = [v for v in old.get("verdicts") or [] if isinstance(v, dict)]
    except (OSError, ValueError, AttributeError):
        have, old_v = {}, []
    merged = {k: list(v) for k, v in have.items()}
    verds = list(old_v)
    for kind, items in answer.items():
        for i, it in enumerate(items):
            known = {f: x for f, x in it.items() if f in ITEMS[kind]["properties"]}
            if known in merged.get(kind, []):
                continue
            lst = merged.setdefault(kind, [])
            lst.append(it)
            verds += [{**v, "index": len(lst) - 1} for v in verdicts if v["kind"] == kind and v["index"] == i]
    return merged, verds


def clear_llm(ctx) -> None:
    """Remove every fact the indexer wrote: llm rows, fields it filled into human rows, provenance and relations."""
    for r in ctx.db.execute("SELECT probe FROM fact_provenance").fetchall():
        try:
            f = (json.loads(r[0]) if r[0] else {}).get("filled")
        except ValueError:
            continue
        if not f:
            continue
        where = " AND ".join(f"{k}=?" for k in f["keys"])
        cur = ctx.db.execute(f"SELECT * FROM {f['table']} WHERE {where}", tuple(f["keys"].values())).fetchone()
        if cur is None or cur["source"] == LLM:
            continue
        for fld, val in f["fields"].items():
            if cur[fld] == val:
                ctx.db.execute(f"UPDATE {f['table']} SET {fld}=? WHERE {where}", [f.get("old", {}).get(fld, ""), *f["keys"].values()])
    for t in ("column_semantics", "table_notes", "concepts", "concept_links", "metrics", "performance_notes"):
        ctx.db.execute(f"DELETE FROM {t} WHERE source=?", (LLM,))
    ctx.db.execute("DELETE FROM fact_provenance")
    ctx.db.execute("DELETE FROM index_relations")
    ctx.db.commit()


# --- edges ---------------------------------------------------------------------------------------------------------

EV = "llm: "


def _pair(e: dict) -> frozenset:
    return frozenset([(e["from"], tuple(e["from_columns"])), (e["to"], tuple(e["to_columns"]))])


def count_existing(g: Graph, answer: dict, verdicts: list[dict] | None = None) -> int:
    """How many relations of an accepted answer repeat an edge the base graph already has (either direction). A repeat
    adds evidence only. Items that failed a probe are not counted."""
    bad = {v["index"] for v in verdicts or [] if v["kind"] == "relations" and v.get("verified") is False}
    pairs = {_pair(e) for e in g.data["edges"]}
    n = 0
    for i, it in enumerate(answer.get("relations") or []):
        ft, tt = _tid(g, it["from_table"]), _tid(g, it["to_table"])
        if i not in bad and ft and tt and frozenset([(ft, tuple(it["from_columns"])), (tt, tuple(it["to_columns"]))]) in pairs:
            n += 1
    return n


def strip_llm(g: Graph) -> None:
    """Undo an earlier application: drop llm edges, their mirror copies, llm evidence and llm confidence raises."""
    llm = [e for e in g.data["edges"] if e.get("source") == LLM]
    copies = {f"copy of {e['from']}({','.join(e['from_columns'])}) -> {e['to']}({','.join(e['to_columns'])}) in the original schema"
              for e in llm}
    g.data["edges"][:] = [e for e in g.data["edges"] if e.get("source") != LLM
                          and not (e.get("source") == "mirror" and set(e.get("evidence") or []) & copies)]
    for e in g.data["edges"]:
        boost = e.pop("llm_boost", None)
        if boost and e["confidence"] == boost[1]:
            e["confidence"] = boost[0]
        e["evidence"] = [x for x in e.get("evidence", []) if not x.startswith(EV)]


def _edge_confidence(row) -> float:
    return CAP_VERIFIED if row["status"] == "verified" else round(min(_clamp(row["confidence"]), CAP_UNVERIFIED), 2)


def _cardinality(g: Graph, table: str, cols: list[str], given: str) -> str:
    if given:
        return given
    n = g.nodes[table]
    if n["primary_key"] == cols or any(u["columns"] == cols for u in n.get("unique_keys", [])):
        return "one-to-one"
    return "many-to-one"


def _human_rejected(rejects: list[dict], ft: str, fc: list, tt: str, tc: list) -> bool:
    """True when a person rejected this pair (an override `reject`), in either direction."""
    for x in rejects:
        for a, ac, b, bc in ((ft, fc, tt, tc), (tt, tc, ft, fc)):
            if x["from"] == a and x["from_columns"] == ac and (not x["to"] or (x["to"] == b and (not x["to_columns"] or x["to_columns"] == bc))):
                return True
    return False


def apply_index_relations(g: Graph, ctx) -> dict:
    """Apply index_relations to the graph (idempotent; call after inference and the context import, before the
    graph is saved). Unverified edges are capped at 0.75 and probe-passed ones sit at 0.9. A rejected candidate stays in
    keygraph.db only. A declared, confirmed or added edge is never changed; an equal inferred edge gets the evidence and
    max(confidence, llm confidence)."""
    strip_llm(g)
    rows = ctx.db.execute("SELECT * FROM index_relations ORDER BY rel_id").fetchall()
    rejects = g.data.get("override_rejects") or []
    existing: dict[frozenset, dict] = {}
    for e in g.data["edges"]:
        existing.setdefault(_pair(e), e)
    out = {"added": 0, "merged": 0, "unchanged": 0, "rejected": 0, "stale": 0, "human_rejected": 0}
    for r in rows:
        if r["status"] == "rejected":
            out["rejected"] += 1
            continue
        fc, tc = json.loads(r["from_columns"]), json.loads(r["to_columns"])
        a, b = g.nodes.get(r["from_table"]), g.nodes.get(r["to_table"])
        names = ({c["name"] for c in a["columns"]}, {c["name"] for c in b["columns"]}) if a and b else None
        if not a or not b or a.get("mirror_of") or b.get("mirror_of") or not fc or len(fc) != len(tc) \
                or not set(fc) <= names[0] or not set(tc) <= names[1]:
            out["stale"] += 1
            continue
        if _human_rejected(rejects, r["from_table"], fc, r["to_table"], tc):
            out["human_rejected"] += 1
            continue
        conf = _edge_confidence(r)
        ev = [EV + (r["reason"] or "proposed relation")]
        if r["status"] == "verified":
            p = json.loads(r["probe"] or "{}")
            ev.append(EV + f"probe passed ({p.get('check', 'check')}" + (f", rate {p['rate']}" if p.get("rate") is not None else "") + ")")
        e = existing.get(frozenset([(r["from_table"], tuple(fc)), (r["to_table"], tuple(tc))]))
        if e is not None:
            if e["kind"] == "declared" or e["status"] in ("declared", "confirmed", "added"):
                out["unchanged"] += 1
                continue
            e["evidence"] = list(e.get("evidence", [])) + ev
            if conf > e["confidence"]:
                e["llm_boost"] = [e["confidence"], conf]
                e["confidence"] = conf
            out["merged"] += 1
            continue
        new = {"kind": "inferred", "status": "inferred", "from": r["from_table"], "from_columns": fc, "to": r["to_table"],
               "to_columns": tc, "confidence": conf, "cardinality": _cardinality(g, r["from_table"], fc, (r["cardinality"] if "cardinality" in dict(r) else "") or ""),
               "ambiguous": False, "evidence": ev, "source": LLM}
        new["id"] = edge_id(new)
        g.data["edges"].append(new)
        existing[_pair(new)] = new
        out["added"] += 1
    g.data["edges"].sort(key=lambda e: e["id"])
    g.refresh_stats()
    return out
