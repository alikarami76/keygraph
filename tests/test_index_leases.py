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
    assert 1 <= len(claimed) < 8 and any(r["status"] == "budget_exhausted" for r in res)
    with ContextDB(work / "keygraph.db") as ctx:
        assert jobs.reserved(ctx, "job-0001") + jobs.get_job(ctx, "job-0001")["used"] <= budget
    # submitting charges the packet once and still fits
    for r in claimed:
        _submit(work, r["unit_id"], next(f"w{i}" for i in range(8) if X.next_result(work, f"w{i}").get("unit_id") == r["unit_id"]))
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
