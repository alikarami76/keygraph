"""Index job state in keygraph.db: a job, its units, attempts, token estimates and the budget used.
The probe source is stored as environment variable NAMES only, never values."""

from __future__ import annotations

import json
import re
import time

from keygraph import __version__

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UNIT_STATUS = ("pending", "done", "failed", "skipped")
TASKS = ("relations", "columns", "tables", "concepts", "metrics", "performance")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _env_name(label: str, value: str) -> str:
    value = (value or "").strip()
    if value and not ENV_NAME.match(value):
        raise ValueError(f"{label} takes the NAME of an environment variable, not its value")
    return value


def _job(r) -> dict:
    return {"job_id": r["job_id"], "budget": r["budget"], "used": r["used"], "tasks": json.loads(r["tasks"] or "[]"),
            "keygraph_version": r["keygraph_version"], "schema_version": r["schema_version"], "status": r["status"],
            "url_env": r["url_env"] or "", "cmd_env": r["cmd_env"] or "", "finished": bool(r["finished_at"])}


def _unit(r) -> dict:
    return {"unit_id": r["unit_id"], "job_id": r["job_id"], "tables": json.loads(r["tables"]), "tasks": json.loads(r["tasks"]),
            "rank": r["rank"], "packet_hash": r["packet_hash"] or "", "status": r["status"], "attempts": r["attempts"],
            "tokens_est": r["tokens_est"]}


def create_job(ctx, units: list[dict], *, budget: int, tasks=TASKS, url_env: str = "", cmd_env: str = "",
               schema_version: int | str = 1) -> str:
    """Create an open job with its pending units and return the job id (job-0001, job-0002, ...)."""
    if int(budget) <= 0:
        raise ValueError("budget must be a positive number of tokens, for example --budget 200000")
    tasks = list(tasks)
    if not tasks:
        raise ValueError(f"tasks is empty; pass at least one of: {', '.join(TASKS)}")
    bad = [t for t in tasks if t not in TASKS]
    if bad:
        raise ValueError(f"unknown task kind: {', '.join(bad)}")
    if url_env and cmd_env:
        raise ValueError("use url_env or cmd_env, not both")
    url_env, cmd_env = _env_name("url_env", url_env), _env_name("cmd_env", cmd_env)
    n = ctx.db.execute("SELECT COALESCE(MAX(rowid), 0) FROM index_jobs").fetchone()[0]
    job_id = f"job-{n + 1:04d}"
    tasks = [t for t in TASKS if t in tasks]
    ctx.db.execute("INSERT INTO index_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, _now(), None, int(budget), 0, json.dumps(tasks), __version__, str(schema_version), "open",
                    url_env, cmd_env))
    ctx.db.executemany("INSERT INTO index_units VALUES(?,?,?,?,?,?,?,?,?,?)",
                       [(u["unit_id"], job_id, json.dumps(u["tables"]), json.dumps(tasks), u["rank"], "", "pending", 0, 0, None)
                        for u in units])
    ctx.db.commit()
    return job_id


def get_job(ctx, job_id: str) -> dict | None:
    r = ctx.db.execute("SELECT * FROM index_jobs WHERE job_id=?", (job_id,)).fetchone()
    return _job(r) if r else None


def open_job(ctx) -> dict | None:
    """The open job to resume (the latest one still open), or None."""
    r = ctx.db.execute("SELECT * FROM index_jobs WHERE status='open' ORDER BY rowid DESC LIMIT 1").fetchone()
    return _job(r) if r else None


def list_units(ctx, job_id: str, status: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM index_units WHERE job_id=?", [job_id]
    if status:
        q, args = q + " AND status=?", args + [status]
    return [_unit(r) for r in ctx.db.execute(q + " ORDER BY rank, unit_id", args)]


def get_unit(ctx, job_id: str, unit_id: str) -> dict | None:
    r = ctx.db.execute("SELECT * FROM index_units WHERE job_id=? AND unit_id=?", (job_id, unit_id)).fetchone()
    return _unit(r) if r else None


def next_unit(ctx, job_id: str) -> dict | None:
    """The best-ranked pending unit, or None when nothing is left."""
    u = list_units(ctx, job_id, "pending")
    return u[0] if u else None


def update_unit(ctx, job_id: str, unit_id: str, *, status: str | None = None, attempts: int | None = None,
                tokens_est: int | None = None, packet_hash: str | None = None) -> dict:
    """Change a unit's status, attempts, tokens_est or packet_hash; others stay as they were."""
    if status is not None and status not in UNIT_STATUS:
        raise ValueError(f"unit status must be one of {', '.join(UNIT_STATUS)}")
    if get_unit(ctx, job_id, unit_id) is None:
        raise KeyError(f"unknown unit {unit_id} in {job_id}")
    sets = {"status": status, "attempts": attempts, "tokens_est": tokens_est, "packet_hash": packet_hash}
    sets = {k: v for k, v in sets.items() if v is not None}
    if status in ("done", "failed"):
        sets["submitted_at"] = _now()
    if sets:
        ctx.db.execute(f"UPDATE index_units SET {', '.join(f'{k}=?' for k in sets)} WHERE job_id=? AND unit_id=?",
                       [*sets.values(), job_id, unit_id])
        ctx.db.commit()
    return get_unit(ctx, job_id, unit_id)


def add_used(ctx, job_id: str, tokens: int) -> int:
    """Spend tokens from the budget; returns the new total used."""
    ctx.db.execute("UPDATE index_jobs SET used=used+? WHERE job_id=?", (int(tokens), job_id))
    ctx.db.commit()
    return get_job(ctx, job_id)["used"]


def budget_left(ctx, job_id: str) -> int:
    j = get_job(ctx, job_id)
    return max(0, j["budget"] - j["used"])


def has_budget(ctx, job_id: str, estimate: int) -> bool:
    """True when a unit costing `estimate` tokens still fits in what is left; hand out no unit otherwise."""
    return budget_left(ctx, job_id) >= int(estimate)


def close_job(ctx, job_id: str, status: str = "done") -> dict:
    """Mark a job finished (status done or aborted); it is no longer resumed."""
    if status not in ("done", "aborted"):
        raise ValueError("close status must be done or aborted")
    ctx.db.execute("UPDATE index_jobs SET status=?, finished_at=? WHERE job_id=?", (status, _now(), job_id))
    ctx.db.commit()
    return get_job(ctx, job_id)


def progress(ctx, job_id: str) -> dict:
    """Counts per unit status plus budget numbers (no timestamps)."""
    j = get_job(ctx, job_id)
    counts = {s: 0 for s in UNIT_STATUS}
    for u in list_units(ctx, job_id):
        counts[u["status"]] += 1
    return {"job_id": job_id, "status": j["status"], "units": counts, "budget": j["budget"], "used": j["used"],
            "left": max(0, j["budget"] - j["used"])}
