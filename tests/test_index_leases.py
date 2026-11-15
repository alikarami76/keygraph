"""Parallel-safe claims for `keygraph index next --worker`: leases, budget reservations and order-independent replay."""

import json
import shutil
import threading

import pytest
from test_example_shop import _build

from keygraph.cli import main
from keygraph.context import ContextDB
from keygraph.index import cli_api as X
from keygraph.index import jobs
from keygraph.index import replay as RP
from keygraph.index import store as ST

EMPTY = {"relations": []}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("index_leases")
    _build(out)
    return out


@pytest.fixture
def work(built, tmp_path):
    out = tmp_path / "out"
    shutil.copytree(built, out)
    return out


@pytest.fixture
def clock(monkeypatch):
    t = [1_000_000.0]
    monkeypatch.setattr(jobs, "_clock", lambda: t[0])
    return t


def _start(out, **kw):
    return X.start_result(out, tasks=["relations"], **kw)


def _submit(out, unit, worker=""):
    return X.submit_text(out, unit, json.dumps(EMPTY), "m", 1, worker)


def test_concurrent_claims_never_return_the_same_unit(work):
    _start(work)
    got, errs = [], []
    gate = threading.Barrier(6)

    def go(i):
        try:
            gate.wait()
            got.append(X.next_result(work, f"w{i}")["unit_id"])
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(got) == len(set(got)) == 6
    # a worker asking again gets its own unit back; units come out in rank order
    with ContextDB(work / "keygraph.db") as ctx:
        ranked = [u["unit_id"] for u in jobs.list_units(ctx, "job-0001")]
    assert sorted(got, key=ranked.index) == ranked[:6]
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.leased_count(ctx, "job-0001") == 6


def test_same_worker_gets_its_unit_back_and_waiting_when_all_leased(work):
    _start(work, max_units=2)
    a = X.next_result(work, "a")
    assert X.next_result(work, "a")["unit_id"] == a["unit_id"]
    b = X.next_result(work, "b")
    assert b["unit_id"] != a["unit_id"]
    w = X.next_result(work, "c")
    assert w["status"] == "waiting" and w["leased"] == 2 and w["retry_after"] >= 1
    _submit(work, a["unit_id"], "a")
    _submit(work, b["unit_id"], "b")
    assert X.next_result(work, "c")["status"] == "done"


def test_expired_lease_returns_the_unit_and_a_stale_submit_is_refused(work, clock):
    _start(work)
    a = X.next_result(work, "a", 10)
    clock[0] += 11
    b = X.next_result(work, "b", 600)
    assert b["unit_id"] == a["unit_id"]  # back in the pool, still the best ranked
    with pytest.raises(ValueError, match="leased to worker b"):
        _submit(work, a["unit_id"], "a")
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.get_unit(ctx, "job-0001", a["unit_id"])["status"] == "pending"
    assert _submit(work, b["unit_id"], "b")["status"] == "accepted"
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.live_lease(ctx, "job-0001", b["unit_id"]) is None  # accepted: lease released
    with pytest.raises(ValueError, match="already done"):
        _submit(work, a["unit_id"], "a")


def test_expired_lease_with_nobody_else_still_accepts_the_submit(work, clock):
    _start(work)
    a = X.next_result(work, "a", 10)
    clock[0] += 11
    assert _submit(work, a["unit_id"], "a")["status"] == "accepted"
    b = X.next_result(work, "y")
    with pytest.raises(ValueError, match="leased to worker y"):
        _submit(work, b["unit_id"], "x")


def test_rejected_unit_keeps_its_lease(work):
    _start(work)
    a = X.next_result(work, "a")
    bad = {"relations": [{"from_table": "nope.nothing", "from_columns": ["x"], "to_table": "shop.orders", "to_columns": ["id"],
                          "reason": "r", "confidence": 0.5}]}
    assert X.submit_result(work, a["unit_id"], bad, "m", 1, "a")["status"] == "rejected"
    assert X.next_result(work, "b")["unit_id"] != a["unit_id"]
    assert X.next_result(work, "a")["unit_id"] == a["unit_id"]
    with pytest.raises(ValueError, match="leased to worker a"):
        X.submit_result(work, a["unit_id"], EMPTY, "m", 1, "b")


def test_parallel_claims_never_overspend_the_budget(work):
    probe = work.parent / "probe"
    shutil.copytree(work, probe)
    _start(probe)
    with ContextDB(probe / "keygraph.db") as ctx:
        X.next_result(probe, "p")
        est = jobs.list_units(ctx, "job-0001")[0]["tokens_est"]
    budget = int(est * 2.5)
    _start(work, budget=budget)
    res, gate = [], threading.Barrier(8)

    def go(i):
        gate.wait()
        res.append(X.next_result(work, f"w{i}"))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    claimed = [r for r in res if r["status"] == "unit"]
    assert 1 <= len(claimed) < 8 and all(r["status"] in ("unit", "waiting", "budget_exhausted") for r in res)
    assert any(r["status"] != "unit" for r in res)
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.reserved(ctx, "job-0001") + jobs.get_job(ctx, "job-0001")["used"] <= budget
    # submitting charges the packet once and still fits (a submit that does not fit is told to wait or raise the budget)
    with ContextDB(work / "keygraph.db") as ctx:
        holders = {r["unit_id"]: r["worker"] for r in ctx.db.execute("SELECT unit_id, worker FROM index_leases")}
    for r in claimed:
        assert _submit(work, r["unit_id"], holders[r["unit_id"]])["status"] in ("accepted", "waiting", "budget_exhausted")
    with ContextDB(work / "keygraph.db") as ctx:
        j = jobs.get_job(ctx, "job-0001")
        assert j["used"] <= budget and jobs.reserved(ctx, "job-0001") == 0


def test_answers_apply_in_rank_order_not_submission_or_name_order(work, monkeypatch):
    _start(work)
    with ContextDB(work / "keygraph.db") as ctx:
        units = jobs.list_units(ctx, "job-0001")
    ids = [u["unit_id"] for u in units]
    assert ids != sorted(ids)  # the demo's rank order differs from id order, so the test can tell them apart
    for u in reversed(units[:6]):  # saved in the opposite order to rank
        ST.save_answer(work, u, "job-0001", {"relations": []}, "m", "h", [])
    seen = []
    real = RP.store_unit
    monkeypatch.setattr(RP, "store_unit", lambda ctx, g, job_id, unit, *a, **k: (seen.append(unit["unit_id"]), real(ctx, g, job_id, unit, *a, **k))[1])
    RP.replay(work)
    assert seen == ids[:6]


def test_replay_is_byte_identical_after_shuffled_submits(built, tmp_path):
    """End-to-end regression check; the discriminating order test is test_replay_order_comes_from_the_answer_files."""
    outs = []
    for name, order in (("fwd", 1), ("rev", -1)):
        d = tmp_path / name
        shutil.copytree(built, d)
        _start(d, max_units=5)
        claimed = {w: X.next_result(d, w)["unit_id"] for w in ("a", "b", "c", "d", "e")}
        for w in list(claimed)[::order]:
            assert _submit(d, claimed[w], w)["status"] == "accepted"
        assert X.next_result(d, "a")["status"] == "done"
        X.finish_result(d)
        outs.append(d)
    for rel in ("graph.json", "index/summary.json"):
        assert (outs[0] / rel).read_bytes() == (outs[1] / rel).read_bytes()
    pages = sorted(p.relative_to(outs[0]) for p in outs[0].rglob("*.md"))
    assert pages and all((outs[0] / p).read_bytes() == (outs[1] / p).read_bytes() for p in pages)


def test_next_without_worker_is_unchanged(work, capsys):
    r = _start(work)
    a = X.next_result(work)
    assert list(a) == ["status", "unit_id", "tasks", "instructions", "packet", "answer_schema", "budget_left", "attempt"]
    assert a["status"] == "unit" and a["attempt"] == 1 and a["budget_left"] == r["budget"]  # the reservation is not subtracted from the claimer
    assert X.next_result(work) == a
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.live_lease(ctx, "job-0001", a["unit_id"])["worker"] == "default"
        assert jobs.get_job(ctx, "job-0001")["used"] == 0
    assert _submit(work, a["unit_id"])["status"] == "accepted"  # no --worker on submit either
    assert main(["index", "next", "--dir", str(work), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unit_id"] != a["unit_id"]


def test_worker_and_lease_arguments_are_checked(work):
    _start(work)
    with pytest.raises(ValueError, match="worker"):
        X.next_result(work, "bad name; drop")
    with pytest.raises(ValueError, match="lease_seconds"):
        X.next_result(work, "a", 0)


def test_index_connections_wait_for_the_lock(work):
    with ContextDB(work / "keygraph.db") as ctx:
        assert ctx.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


# --- round 1: ledger, lease renewal, transient budget, busy database, rank in answer files ----------------------------

def _two_units(out):
    with ContextDB(out / "keygraph.db") as ctx:
        return jobs.list_units(ctx, "job-0001")


def test_concurrent_submits_keep_every_ledger_entry(work, monkeypatch):
    _start(work)
    a, b = X.next_result(work, "a")["unit_id"], X.next_result(work, "b")["unit_id"]
    a_in_probe, b_done = threading.Event(), threading.Event()
    real = X.PR.probe_items

    def probe(*args, **kw):
        if threading.current_thread().name == "ta":
            a_in_probe.set()
            assert b_done.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(X.PR, "probe_items", probe)
    errs = []

    def run(name, unit, worker):
        try:
            if name == "tb":
                assert a_in_probe.wait(10)
            _submit(work, unit, worker)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)
        finally:
            if name == "tb":
                b_done.set()

    ts = [threading.Thread(target=run, args=("ta", a, "a"), name="ta"), threading.Thread(target=run, args=("tb", b, "b"), name="tb")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    led = json.loads((work / "index" / "ledger.json").read_text(encoding="utf-8"))
    assert {a, b} <= set(led) and led[a]["accepted"] == 0 and led[a]["attempts"] == 1 and led[b]["attempts"] == 1


def test_a_torn_ledger_is_never_seen(work):
    _start(work)
    X.next_result(work, "a")
    X._save_ledger(work, {"x": {"accepted": 1}})
    assert not list((work / "index").glob("*.tmp*"))  # written through a temp file and renamed


def test_lease_is_renewed_when_a_submit_starts_its_write_window(work, clock, monkeypatch):
    _start(work)
    a = X.next_result(work, "a", 10)
    seen = {}
    real = X.PR.probe_items

    def probe(*args, **kw):
        clock[0] += 500  # the probes outlast the original 10 s lease
        seen["b"] = X.next_result(work, "b")
        return real(*args, **kw)

    monkeypatch.setattr(X.PR, "probe_items", probe)
    assert _submit(work, a["unit_id"], "a")["status"] == "accepted"
    assert seen["b"]["unit_id"] != a["unit_id"]  # b could not take the unit a was storing
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.get_unit(ctx, "job-0001", a["unit_id"])["status"] == "done"


def _intrude(monkeypatch, action):
    """Run `action` once, just before a submit opens its write transaction (after its first checks passed)."""
    real, done = jobs.begin_write, []

    def begin(ctx):
        if not done:
            done.append(1)
            action()
        return real(ctx)

    monkeypatch.setattr(X.jobs, "begin_write", begin)


def test_write_window_rechecks_status_lease_and_budget(work, clock, monkeypatch):
    _start(work)
    a = X.next_result(work, "a", 10)["unit_id"]
    clock[0] += 11
    _intrude(monkeypatch, lambda: _submit(work, a, "b"))  # b stores the unit while a is between its checks
    with pytest.raises(ValueError, match="already (done|submitted)"):
        _submit(work, a, "a")
    with ContextDB(work / "keygraph.db") as ctx:
        used = jobs.get_job(ctx, "job-0001")["used"]
    assert used > 0
    # a competitor takes the expired unit between a's first check and its write window
    c = X.next_result(work, "c", 10)["unit_id"]
    clock[0] += 11
    _intrude(monkeypatch, lambda: X.next_result(work, "d"))
    with pytest.raises(ValueError, match="leased to worker d"):
        _submit(work, c, "c")
    # the budget is checked again inside the window
    e = X.next_result(work, "e")["unit_id"]

    def spend():
        with ContextDB(work / "keygraph.db") as ctx:
            ctx.db.execute("UPDATE index_jobs SET used=budget WHERE job_id='job-0001'")

    _intrude(monkeypatch, spend)
    assert _submit(work, e, "e")["status"] == "budget_exhausted"


def test_two_workers_submitting_one_expired_unit_store_it_once(work, clock):
    _start(work)
    a = X.next_result(work, "a", 10)["unit_id"]
    clock[0] += 11
    out, gate = [], threading.Barrier(2)

    def go(w):
        gate.wait()
        try:
            out.append(_submit(work, a, w)["status"])
        except ValueError as exc:
            out.append("refused: " + str(exc))

    ts = [threading.Thread(target=go, args=(w,)) for w in ("b", "c")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(o.split(":")[0] for o in out) == ["accepted", "refused"]
    with ContextDB(work / "keygraph.db") as ctx:
        u = jobs.get_unit(ctx, "job-0001", a)
        assert u["status"] == "done" and u["attempts"] == 1
        assert jobs.get_job(ctx, "job-0001")["used"] < 2 * u["tokens_est"]  # charged once


def test_budget_held_by_other_workers_is_waiting_not_exhausted(work, built, tmp_path):
    probe = tmp_path / "probe"
    shutil.copytree(built, probe)
    _start(probe)
    X.next_result(probe, "p")
    X.next_result(probe, "q")
    e1, e2 = [u["tokens_est"] for u in _two_units(probe)[:2]]
    _start(work, budget=e1 + e2 - 1)  # each unit fits alone, both together do not
    assert X.next_result(work, "a")["status"] == "unit"
    r = X.next_result(work, "b")
    assert r["status"] == "waiting" and r["retry_after"] >= 1 and "resume" not in r
    assert X.next_result(work, "a")["status"] == "unit"


def test_database_busy_gives_a_friendly_error(work, monkeypatch):
    import sqlite3

    from keygraph import context
    _start(work)
    monkeypatch.setattr(context, "BUSY_SECONDS", 0.1)
    blocker = sqlite3.connect(work / "keygraph.db")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ValueError, match="busy"):
            X.next_result(work, "a")
    finally:
        blocker.rollback()
        blocker.close()


def test_lease_seconds_is_capped(work):
    _start(work)
    with pytest.raises(ValueError, match="3600"):
        X.next_result(work, "a", 3601)
    assert X.next_result(work, "a", 3600)["status"] == "unit"


def _snapshot(d):
    return b"".join(p.read_bytes() for p in [d / "graph.json", d / "keygraph.html", *sorted(d.glob("tables/*.md"))])


def _order_sensitive(out, ranks):
    units = _two_units(out)[:2]
    ST.save_answer(out, {**units[0], "rank": ranks[0]}, "job-0001", {"tables": [
        {"table": "shop.carts", "purpose": "Basket A", "default_filters": ["status = 1"], "reason": "r", "confidence": 0.7}]}, "m", "h", [])
    ST.save_answer(out, {**units[1], "rank": ranks[1]}, "job-0001", {"tables": [
        {"table": "shop.carts", "purpose": "Basket B", "default_filters": ["status = 2"], "reason": "r", "confidence": 0.7}]}, "m", "h", [])


def test_replay_order_comes_from_the_answer_files_and_discriminates(built, tmp_path):
    outs = {}
    for name, ranks in (("rank", (0, 1)), ("swapped", (1, 0))):
        d = tmp_path / name
        shutil.copytree(built, d)
        _start(d)
        _order_sensitive(d, ranks)
        outs[name] = d
    for d in outs.values():
        RP.replay(d)
    assert _snapshot(outs["rank"]) != _snapshot(outs["swapped"])  # the test can tell orders apart
    # same ranks, files written in the other order, and the index tables gone: replay uses the files alone
    other = tmp_path / "other"
    shutil.copytree(built, other)
    _start(other)
    ST.save_answer(other, {**_two_units(other)[1], "rank": 1}, "job-0001", {"tables": [
        {"table": "shop.carts", "purpose": "Basket B", "default_filters": ["status = 2"], "reason": "r", "confidence": 0.7}]}, "m", "h", [])
    ST.save_answer(other, {**_two_units(other)[0], "rank": 0}, "job-0001", {"tables": [
        {"table": "shop.carts", "purpose": "Basket A", "default_filters": ["status = 1"], "reason": "r", "confidence": 0.7}]}, "m", "h", [])
    with ContextDB(other / "keygraph.db") as ctx:
        ctx.db.execute("DELETE FROM index_units")
    RP.replay(other)
    assert _snapshot(other) == _snapshot(outs["rank"])
