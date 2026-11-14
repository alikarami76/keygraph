"""The functions behind `keygraph index ...` and the MCP index tools: each returns the JSON dict the CLI prints with --json
and the tool returns, so the two can never differ.

Protocol: `start` plans units and makes a job, `next` hands out the best pending unit (the same one until it is submitted),
`submit` validates, probes and stores one answer, `status` reports, `finish` and `replay` rebuild the map from the saved
answers, `refresh` re-plans after the map changed.

Packets are always built from the deterministic base graph (`index/base_graph.json`) and from the context as it was before
any indexing fact was written (a throw-away in-memory copy with the llm layer removed), so a unit's packet hash does not
change just because the agent answered it. Only a real change in the map makes `refresh` mark a unit as pending.

Probes: keygraph reads the probe source from the environment variable NAMED in the job at submit time. Nothing but names,
counts and rates is stored, logged or returned. Dev mode logs ids, sizes, counts and check names, never values or reasons."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from .. import devlog
from ..context import ContextDB
from . import jobs
from . import packet as PK
from . import plan as PL
from . import probe as PR
from . import replay as RP
from . import schema as SC
from . import store as ST
from . import validate as VL

DEFAULT_BUDGET = 200_000
MAX_ATTEMPTS = 3
NEXT, FINISH = "keygraph index next", "keygraph index finish"
DIALECTS = ("mysql", "mariadb", "postgres", "postgresql", "tsql", "sqlserver", "clickhouse", "sqlite")
LEDGER = "ledger.json"
REASONS_KEPT = 20


# --- helpers ---------------------------------------------------------------------------------------------------

def _need_map(out: Path) -> None:
    if not (out / "graph.json").exists():
        raise ValueError(f"no map in {out}: build one first with `keygraph build` (information_schema dumps) or "
                         f"`keygraph scan --url ...` (live database); the demo builds with examples/shop/build.sh")


def _base(out: Path):
    _need_map(out)
    try:
        return RP._load_base(out)
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from None


def _pre_llm(ctx):
    """A context without the indexer's facts: ctx itself when it holds none, else an in-memory copy with them removed."""
    if not ctx.db.execute("SELECT COUNT(*) FROM fact_provenance").fetchone()[0]:
        return ctx
    mem = ContextDB(":memory:")
    ctx.db.backup(mem.db)
    ST.clear_llm(mem)
    return mem


def _packet(g, ctx, unit: dict) -> tuple[dict, int, str]:
    pk = PK.build_packet(g, _pre_llm(ctx), unit["tables"])
    return pk, PK.estimate(pk), hashlib.sha256(SC.canonical(pk).encode("utf-8")).hexdigest()


def _ledger(out: Path) -> dict:
    try:
        d = json.loads((out / "index" / LEDGER).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(out: Path, d: dict) -> None:
    p = out / "index" / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")


def _entry(led: dict, uid: str) -> dict:
    return led.setdefault(uid, {"accepted": 0, "attempts": 0, "probe_failed": 0, "reasons": [], "rejected": 0, "unverified": 0,
                                "verified": 0})


RESUBMIT_HINT = "resubmit only the rejected items for this unit_id; accepted items are already stored"
FINISHED_HINT = ("the job is finished; run `keygraph index refresh` to index changed tables or "
                 "`keygraph index start --restart` to redo all")


def _finished(ctx) -> dict | None:
    """{status: finished, hint} when the newest job is done and nothing is open: submit and next end the loop here."""
    last = jobs.latest_job(ctx)
    return {"status": "finished", "hint": FINISHED_HINT} if last and last["status"] == "done" else None


def _tasks(tasks) -> list[str]:
    if tasks is None or tasks == "" or tasks == []:
        return list(SC.TASKS)
    items = [t.strip() for t in (tasks.split(",") if isinstance(tasks, str) else tasks) if str(t).strip()]
    return SC.kinds_of(items)


def _env_names(url_env: str, cmd_env: str) -> None:
    """Check the names (never echo a value: someone may have typed the URL or a command here) and that the variable is set."""
    if url_env and cmd_env:
        raise ValueError("use --url-env or --cmd-env, not both")
    for flag, name in (("--url-env", url_env), ("--cmd-env", cmd_env)):
        if not name:
            continue
        if not jobs.ENV_NAME.match(name):
            raise ValueError(f"{flag} takes the NAME of an environment variable (for example SHOP_DB_URL), not its value")
        if not os.environ.get(name):
            raise ValueError(f"environment variable {name} is not set (needed by {flag}): export it first, "
                             f"or leave {flag} out to index without probes")


def _job_view(ctx, job: dict, resumed: bool) -> dict:
    counts = jobs.progress(ctx, job["job_id"])["units"]
    return {"job_id": job["job_id"], "units": sum(counts.values()), "budget": job["budget"], "used": job["used"],
            "pending": counts["pending"], "skipped": counts["skipped"], "tasks": job["tasks"], "url_env": job["url_env"],
            "cmd_env": job["cmd_env"], "dialect": job["dialect"], "resumed": resumed, "next": NEXT}


# --- start -----------------------------------------------------------------------------------------------------

def start_result(dir, budget: int = DEFAULT_BUDGET, tasks=None, url_env: str = "", cmd_env: str = "", dialect: str = "",
                 max_units: int = 0, restart: bool = False) -> dict:
    """Plan the units and create the job, or resume the open one. Needs a built map in `dir`.

    Resume: an open job is returned as it is (`resumed: true`); a larger `budget` raises its budget, and a given url_env,
    cmd_env or dialect replaces the stored one (so `start --budget 400000` is how a run continues after budget_exhausted).
    A finished job is not restarted by accident: use `refresh` (only what changed) or `restart` (a new job with every unit
    pending; answers already saved stay and still count until a unit is submitted again).
    max_units N plans every unit but marks all after the first N (by rank) as skipped: they keep the deterministic map."""
    out = Path(dir)
    _need_map(out)
    _env_names(url_env or "", cmd_env or "")
    dialect = (dialect or "").strip().lower()
    if dialect and dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; choose one of {', '.join(DIALECTS)}")
    budget, max_units = int(budget), int(max_units or 0)
    if budget <= 0:
        raise ValueError("budget must be a positive number of tokens, for example --budget 200000")
    want = _tasks(tasks)
    with ContextDB(out / "keygraph.db") as ctx:
        job = jobs.latest_job(ctx)
        if job and not restart:
            if job["status"] == "open":
                ignored = ["tasks"] if tasks and want != job["tasks"] else []
                job = jobs.update_job(ctx, job["job_id"], budget=max(budget, job["budget"]),
                                      url_env=url_env or None, cmd_env=cmd_env or None, dialect=dialect or None)
                res = _job_view(ctx, job, True)
                if ignored:
                    res["ignored"] = ignored
                devlog.decide("index_start", job_id=job["job_id"], resumed=True, units=res["units"], budget=res["budget"],
                              used=res["used"])
                return res
            raise ValueError(f"{job['job_id']} is finished: run `keygraph index refresh` to index only what changed, "
                             f"or `keygraph index start --restart` for a full new run")
        if job and job["status"] == "open":
            jobs.close_job(ctx, job["job_id"], "aborted")
        RP.snapshot_base(out)
        g = _base(out)
        units = PL.make_units(g, PL.rank_tables(g, _pre_llm(ctx)))
        job_id = jobs.create_job(ctx, units, budget=budget, tasks=want, url_env=url_env or "", cmd_env=cmd_env or "",
                                 dialect=dialect, skip_after=max_units if max_units > 0 else None)
        _save_ledger(out, {})
        res = _job_view(ctx, jobs.get_job(ctx, job_id), False)
    devlog.decide("index_start", job_id=job_id, resumed=False, units=res["units"], budget=res["budget"], skipped=res["skipped"],
                  tasks=len(want), probe=("url_env" if url_env else "cmd_env" if cmd_env else "none"))
    return res


# --- next ------------------------------------------------------------------------------------------------------

def next_result(dir) -> dict:
    """The best pending unit with its packet, or budget_exhausted, or done. The same unit comes back until it is submitted
    (or fails after three rejections)."""
    out = Path(dir)
    g = _base(out)
    with ContextDB(out / "keygraph.db", g) as ctx:
        job = jobs.open_job(ctx)
        unit = jobs.next_unit(ctx, job["job_id"]) if job else None
        if job is None and jobs.latest_job(ctx) is None:
            raise ValueError("no index job: run `keygraph index start` first")
        if job is None and _finished(ctx):
            return _finished(ctx)
        if unit is None:
            devlog.decide("index_next", status="done")
            return {"status": "done", "finish": FINISH}
        pk, est, h = _packet(g, ctx, unit)
        jobs.update_unit(ctx, job["job_id"], unit["unit_id"], tokens_est=est, packet_hash=h)
        left = jobs.budget_left(ctx, job["job_id"])
        if not jobs.has_budget(ctx, job["job_id"], est):
            devlog.decide("index_next", status="budget_exhausted", unit_id=unit["unit_id"], tokens_est=est, budget_left=left)
            more = max(job["budget"] * 2, job["used"] + est)
            return {"status": "budget_exhausted", "unit_id": unit["unit_id"], "budget_left": left, "needed": est,
                    "resume": f"keygraph index start --budget {more}"}
        tasks = unit["tasks"]
        devlog.decide("index_next", status="unit", unit_id=unit["unit_id"], tokens_est=est, attempt=unit["attempts"] + 1,
                      budget_left=left, trimmed=len(pk.get("trimmed") or []))
        return {"status": "unit", "unit_id": unit["unit_id"], "tasks": tasks, "instructions": SC.instructions(tasks), "packet": pk,
                "answer_schema": SC.answer_schema(tasks), "budget_left": left, "attempt": unit["attempts"] + 1}


# --- submit ----------------------------------------------------------------------------------------------------

def _source(job: dict, g) -> tuple[PR.Source | None, str]:
    """(probe source, label): label is none (not configured), unset (the named variable is empty now), url, cmd or error."""
    if not (job["url_env"] or job["cmd_env"]):
        return None, "none"
    name = job["url_env"] or job["cmd_env"]
    if not os.environ.get(name):
        return None, "unset"
    try:
        src = PR.runner_from_env(job["url_env"], job["cmd_env"], dialect=job["dialect"] or PR.dialect_of(g))
    except Exception as exc:  # noqa: BLE001  (a URL may be in the message: keep the type only)
        devlog.decide("index_probe_source", error=type(exc).__name__)
        return None, "error"
    return src, "url" if job["url_env"] else "cmd"


def submit_text(dir, unit_id: str, text: str, model: str = "", probe_seconds: float = PR.TIME_BUDGET) -> dict:
    """submit for an answer given as JSON text (the CLI). Text that is not JSON is an error that uses no attempt."""
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the answer is not valid JSON ({type(exc).__name__}: {getattr(exc, 'msg', 'parse error')} at line "
                         f"{getattr(exc, 'lineno', '?')}, column {getattr(exc, 'colno', '?')}); fix the file and submit again, "
                         f"this did not use an attempt") from None
    return submit_result(dir, unit_id, answer, model, probe_seconds)


def _resubmit(unit_id: str, mcp: bool) -> str:
    return f"index_submit unit_id={unit_id}" if mcp else f"keygraph index submit {unit_id} FILE|-"


def submit_result(dir, unit_id: str, answer, model: str = "", probe_seconds: float = PR.TIME_BUDGET, *, mcp: bool = False) -> dict:
    """Check one answer (structure, validation, probes), store what passed and charge the budget.

    Returns {status: accepted | partially_accepted | rejected | failed, accepted, rejected: [{item, reason}], unverified,
    verified, probe_failed, budget_left, probe_source, next} plus attempts_left, resubmit and hint when the unit is still
    open or failed. A structure error, or items rejected and none accepted, is `rejected`: nothing is stored. When some
    items are accepted and some rejected the status is `partially_accepted`: the accepted items are stored, the unit stays
    pending and the agent resubmits only the rejected ones (the saved answer is the union). Every rejected or partial
    submission uses one of three attempts; the third one marks the unit failed. The packet is charged once per unit.
    `mcp` only changes the wording of `next`. A finished job returns {status: finished, hint}."""
    out = Path(dir)
    g = _base(out)
    with ContextDB(out / "keygraph.db", g) as ctx:
        job = jobs.open_job(ctx)
        if job is None:
            fin = _finished(ctx)
            if fin:
                return fin
            raise ValueError("no index job: run `keygraph index start` first")
        job_id = job["job_id"]
        unit = jobs.get_unit(ctx, job_id, unit_id)
        if unit is None:
            raise ValueError(f"unit {unit_id} is not in {job_id}; ask for one with `keygraph index next`")
        if unit["status"] != "pending":
            raise ValueError(f"unit {unit_id} is already {unit['status']}; `keygraph index refresh` puts "
                             + ("failed units and units with changed tables back" if unit["status"] == "failed"
                                else "it back only when its tables changed"))
        tasks = unit["tasks"]
        pk, est, ph = _packet(g, ctx, unit)
        prompt = SC.prompt_hash(SC.instructions(tasks), SC.answer_schema(tasks), pk)
        if not model and isinstance(answer, dict) and isinstance(answer.get("model"), str):
            model = answer["model"]
        model = (model or "unknown").strip()[:120] or "unknown"
        atoks = PK.estimate(answer)
        led = _ledger(out)
        ent = _entry(led, unit_id)
        again = bool(ent.get("partial"))  # an earlier submit stored some items and left the rest to be resubmitted
        val = VL.validate(answer, g, ctx, pk, tasks, require_all=not again)
        rejected = [{"item": "$", "reason": e[3:] if e.startswith("$: ") else e} for e in val["errors"]] + \
                   [{"item": r["item"], "reason": r["reason"]} for r in val["rejected"]]
        attempts = unit["attempts"] + 1
        jobs.add_used(ctx, job_id, atoks + (est if unit["attempts"] == 0 else 0))  # the packet is paid for once per unit
        left = jobs.budget_left(ctx, job_id)
        failed = bool(rejected) and attempts >= MAX_ATTEMPTS
        if val["errors"] or (rejected and not val["accepted"]):
            jobs.update_unit(ctx, job_id, unit_id, status="failed" if failed else None, attempts=attempts, tokens_est=est,
                             packet_hash=ph)
            ent.update(attempts=attempts, rejected=ent["rejected"] + len(rejected), reasons=rejected[:REASONS_KEPT])
            if failed:
                ent.pop("partial", None)
            _save_ledger(out, led)
            devlog.decide("index_submit", unit_id=unit_id, status="failed" if failed else "rejected", attempt=attempts,
                          tokens_est=est, answer_tokens=atoks, accepted=0, rejected=len(rejected), errors=len(val["errors"]))
            res = {"status": "failed" if failed else "rejected", "accepted": 0, "rejected": rejected, "unverified": 0,
                   "verified": 0, "probe_failed": 0, "attempts_left": MAX_ATTEMPTS - attempts if not failed else 0,
                   "budget_left": left, "probe_source": "none"}
            if failed:
                return {**res, "next": "index_next" if mcp else NEXT}
            return {**res, "resubmit": True, "next": _resubmit(unit_id, mcp),
                    "hint": RESUBMIT_HINT if again else "nothing was stored; fix the listed problems and submit the answer "
                                                        "again for this unit_id"}
        src, label = _source(job, g)
        verdicts = PR.probe_items(val["accepted"], g, src.runner if src else None, src.quote if src else None,
                                  bare=src.bare if src else False, time_budget=probe_seconds)
        acc, verds = ST.accepted_answer(val, verdicts)
        if not again:
            ctx.db.execute("DELETE FROM index_relations WHERE unit_id=?", (unit_id,))
        ST.store_unit(ctx, g, job_id, unit, acc, model, prompt, verds)
        if rejected:
            jobs.update_unit(ctx, job_id, unit_id, status="failed" if failed else None, attempts=attempts, tokens_est=est,
                             packet_hash=ph)
        else:
            jobs.update_unit(ctx, job_id, unit_id, status="done", attempts=attempts, tokens_est=est, packet_hash=ph)
        # The answer file comes last: facts and unit status are committed first, so a crash in between leaves no file for a
        # unit that is not done (replay reads files only); a resubmit stores the same items again. Not atomic as a whole.
        saved, saved_v = ST.merge_saved(out, unit, acc, verds) if again else (acc, verds)
        ST.save_answer(out, unit, job_id, saved, model, prompt, saved_v)
        n_ok = sum(1 for v in verds if v["verified"] is True)
        n_bad = sum(1 for v in verds if v["verified"] is False)
        n_unk = len(verds) - n_ok - n_bad
        ent.update(attempts=attempts, accepted=ent["accepted"] + len(val["accepted"]), rejected=ent["rejected"] + len(rejected),
                   verified=ent["verified"] + n_ok, unverified=ent["unverified"] + n_unk,
                   probe_failed=ent["probe_failed"] + n_bad, reasons=rejected[:REASONS_KEPT])
        if rejected and not failed:
            ent["partial"] = True
        else:
            ent.pop("partial", None)
        _save_ledger(out, led)
        status = "failed" if failed else "partially_accepted" if rejected else "accepted"
        devlog.decide("index_submit", unit_id=unit_id, status=status, attempt=attempts, tokens_est=est, answer_tokens=atoks,
                      accepted=len(val["accepted"]), rejected=len(rejected), errors=0, verified=n_ok, unverified=n_unk,
                      probe_failed=n_bad, probe_source=label, checks=dict(sorted(Counter(v["check"] for v in verds).items())))
        res = {"status": status, "accepted": len(val["accepted"]), "rejected": rejected, "unverified": n_unk, "verified": n_ok,
               "probe_failed": n_bad, "budget_left": left, "probe_source": label}
        if status == "accepted":
            return {**res, "next": "index_next" if mcp else NEXT}
        if status == "failed":
            return {**res, "attempts_left": 0, "next": "index_next" if mcp else NEXT}
        return {**res, "attempts_left": MAX_ATTEMPTS - attempts, "resubmit": True, "hint": RESUBMIT_HINT,
                "next": _resubmit(unit_id, mcp)}


# --- status, finish, replay, refresh ---------------------------------------------------------------------------

def status_result(dir) -> dict:
    """Progress, budget, accept and verify rates (null before any submission) and the failed units with their reasons."""
    out = Path(dir)
    _need_map(out)
    with ContextDB(out / "keygraph.db") as ctx:
        job = jobs.latest_job(ctx)
        if job is None:
            raise ValueError("no index job: run `keygraph index start` first")
        prog = jobs.progress(ctx, job["job_id"])
        failed = jobs.list_units(ctx, job["job_id"], "failed")
    led = _ledger(out)
    mine = {u["unit_id"]: led.get(u["unit_id"], {}) for u in failed}
    tot = Counter()
    for e in led.values():
        tot.update({f: e.get(f, 0) for f in ("accepted", "rejected", "verified", "unverified", "probe_failed")})
    judged = tot["accepted"] + tot["rejected"]
    return {"job_id": job["job_id"], "status": job["status"], "units": prog["units"], "total": sum(prog["units"].values()),
            "budget": job["budget"], "used": job["used"], "left": prog["left"],
            "accepted": tot["accepted"], "rejected": tot["rejected"], "verified": tot["verified"],
            "unverified": tot["unverified"], "probe_failed": tot["probe_failed"],
            "accept_rate": round(tot["accepted"] / judged, 3) if judged else None,
            "verify_rate": round(tot["verified"] / tot["accepted"], 3) if tot["accepted"] else None,
            "failed_units": [{"unit_id": u["unit_id"], "tables": u["tables"], "attempts": u["attempts"],
                              "reasons": mine[u["unit_id"]].get("reasons", [])} for u in failed],
            "next": NEXT if prog["units"]["pending"] else FINISH}


def finish_result(dir) -> dict:
    """Replay the saved answers onto the base graph, write index/summary.json and close the job when nothing is pending.
    Returns the summary (counts only), the same dict `replay` returns."""
    out = Path(dir)
    _base(out)
    res = RP.finish(out)
    with ContextDB(out / "keygraph.db") as ctx:
        job = jobs.open_job(ctx)
        if job and not jobs.list_units(ctx, job["job_id"], "pending"):
            jobs.close_job(ctx, job["job_id"], "done")
    devlog.decide("index_finish", units=res["units"], kept=res["kept"], filled=res["filled"], probe_failed=res["probe_failed"],
                  llm_edges=res["llm_edges"])
    return res


def replay_result(dir) -> dict:
    """Rebuild the meaning layer from index/answers/ alone: no model, no environment variable, no source database."""
    out = Path(dir)
    _base(out)
    res = RP.replay(out)
    devlog.decide("index_replay", units=res["units"], kept=res["kept"], filled=res["filled"], llm_edges=res["llm_edges"])
    return res


def refresh_result(dir) -> dict:
    """Re-plan on the current map. Failed units, and done units whose packet hash changed, go back to pending (retried and changed), tables
    that no unit covers get new pending units, and units that are no longer planned are dropped with their saved answers.
    A changed unit id is a new unit, so its old unit is dropped. The base graph is re-saved from the current graph.json."""
    out = Path(dir)
    _need_map(out)
    with ContextDB(out / "keygraph.db") as ctx0:
        job = jobs.latest_job(ctx0)
    if job is None:
        raise ValueError("no index job to refresh: run `keygraph index start` first")
    RP.snapshot_base(out)
    g = _base(out)
    changed: list[str] = []
    retried: list[str] = []
    added: list[dict] = []
    dropped: list[str] = []
    kept = 0
    with ContextDB(out / "keygraph.db", g) as ctx:
        job_id = job["job_id"]
        pre = _pre_llm(ctx)
        new = PL.make_units(g, PL.rank_tables(g, pre))
        old = {u["unit_id"]: u for u in jobs.list_units(ctx, job_id)}
        for u in new:
            o = old.get(u["unit_id"])
            if o is None:
                added.append(u)
                continue
            if o["status"] in ("done", "failed"):
                h = hashlib.sha256(SC.canonical(PK.build_packet(g, pre, u["tables"])).encode("utf-8")).hexdigest()
                if h != o["packet_hash"] or o["status"] == "failed":  # a failed unit gets another go even when nothing changed
                    jobs.reset_unit(ctx, job_id, u["unit_id"], u["rank"])
                    (changed if h != o["packet_hash"] else retried).append(u["unit_id"])
                    continue
            jobs.set_rank(ctx, job_id, u["unit_id"], u["rank"])
            kept += 1
        keep = {u["unit_id"] for u in new}
        led = _ledger(out)
        for uid in sorted(set(old) - keep):
            jobs.drop_unit(ctx, job_id, uid)
            (ST.answers_dir(out) / f"{uid}.json").unlink(missing_ok=True)
            led.pop(uid, None)
            dropped.append(uid)
        for uid in changed + retried:
            led.pop(uid, None)
        _save_ledger(out, led)
        jobs.add_units(ctx, job_id, added, job["tasks"])
        if (changed or retried or added) and job["status"] != "open":
            jobs.reopen_job(ctx, job_id)
        pending = jobs.progress(ctx, job_id)["units"]["pending"]
    devlog.decide("index_refresh", job_id=job_id, changed=len(changed), retried=len(retried), new=len(added), dropped=len(dropped), unchanged=kept)
    return {"job_id": job_id, "changed": sorted(changed), "retried": sorted(retried), "new": sorted(u["unit_id"] for u in added), "dropped": dropped,
            "unchanged": kept, "pending": pending, "next": NEXT if pending else FINISH}
