"""keygraph index start|next|submit|status|finish|replay|refresh: the work-queue protocol through the CLI."""

import json
import shutil
from pathlib import Path

import pytest
from test_example_shop import _build

from keygraph.cli import main
from keygraph.context import ContextDB
from keygraph.index import jobs

ORDERS_UNIT = "949700962b66"  # payments, customers, order_items, orders, sellers
EMPTY = {"relations": []}
BAD = {"relations": [{"from_table": "nope.nothing", "from_columns": ["x"], "to_table": "shop.orders", "to_columns": ["id"],
                      "reason": "made up", "confidence": 0.5}]}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("index_cli")
    _build(out)
    return out


@pytest.fixture
def work(built, tmp_path):
    out = tmp_path / "out"
    shutil.copytree(built, out)
    return out


def run(capsys, out, *argv, expect=0):
    capsys.readouterr()
    code = main([*argv, "--dir", str(out), "--json"])
    cap = capsys.readouterr()
    assert code == expect, cap.err
    return json.loads(cap.out) if cap.out.strip() else None


def submit(capsys, out, tmp_path, unit, answer, *argv, expect=0):
    f = tmp_path / "answer.json"
    f.write_text(json.dumps(answer), encoding="utf-8")
    return run(capsys, out, "index", "submit", unit, str(f), *argv, expect=expect)


def test_start_without_a_map_names_the_build_commands(tmp_path, capsys):
    assert main(["index", "start", "--dir", str(tmp_path / "none")]) == 2
    err = capsys.readouterr().err
    assert "keygraph build" in err and "keygraph scan" in err


def test_unset_env_var_error_names_only_the_variable(work, capsys, monkeypatch):
    monkeypatch.delenv("KG_NOT_SET_URL", raising=False)
    assert main(["index", "start", "--url-env", "KG_NOT_SET_URL", "--dir", str(work)]) == 2
    err = capsys.readouterr().err
    assert "KG_NOT_SET_URL" in err and "://" not in err
    assert not (work / "index").exists()  # nothing was planned
    # someone puts the URL itself where the NAME goes: it is never echoed
    assert main(["index", "start", "--url-env", "mysql://root:hunter2@db.internal/shop", "--dir", str(work)]) == 2
    err = capsys.readouterr().err
    assert "hunter2" not in err and "db.internal" not in err and "NAME" in err
    assert main(["index", "start", "--cmd-env", "KG_NOT_SET_CMD", "--dir", str(work)]) == 2
    assert "KG_NOT_SET_CMD" in capsys.readouterr().err


def test_start_next_and_idempotence(work, capsys):
    r = run(capsys, work, "index", "start")
    assert r["job_id"] == "job-0001" and r["units"] == 18 and r["budget"] == 200000 and r["next"] == "keygraph index next"
    assert (work / "index" / "base_graph.json").exists()
    a, b = run(capsys, work, "index", "next"), run(capsys, work, "index", "next")
    assert a == b and a["status"] == "unit" and a["unit_id"] == ORDERS_UNIT and a["attempt"] == 1
    assert {"tasks", "instructions", "packet", "answer_schema", "budget_left"} <= set(a)
    assert a["packet"]["unit_tables"] == sorted(a["packet"]["unit_tables"]) and "shop.orders" in a["packet"]["unit_tables"]
    # a second start resumes the same job
    again = run(capsys, work, "index", "start")
    assert again["job_id"] == "job-0001" and again["resumed"] is True
    assert run(capsys, work, "index", "start", "--restart")["job_id"] == "job-0002"


def test_budget_exhaustion_resumes_after_start_with_a_larger_budget(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--budget", "100")
    ex = run(capsys, work, "index", "next")
    assert ex["status"] == "budget_exhausted" and ex["resume"].startswith("keygraph index start --budget ") and ex["needed"] > 100
    # `start` with a bigger budget resumes the open job and raises its budget; nothing is lost
    r = run(capsys, work, "index", "start", "--budget", "200000")
    assert r["job_id"] == "job-0001" and r["resumed"] is True and r["budget"] == 200000
    assert run(capsys, work, "index", "next")["status"] == "unit"
    # --restart is the other way: a new job with every unit pending
    run(capsys, work, "index", "start", "--budget", "100", "--restart")
    assert run(capsys, work, "index", "next")["status"] == "budget_exhausted"
    assert run(capsys, work, "index", "start", "--budget", "100", "--restart")["job_id"] == "job-0003"


def test_submit_charges_packet_plus_answer_and_stops_at_the_budget(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations", "--budget", "7000")
    nxt = run(capsys, work, "index", "next")
    r = submit(capsys, work, tmp_path, nxt["unit_id"], EMPTY)
    st = run(capsys, work, "index", "status")
    assert r["status"] == "accepted" and st["used"] > 0 and st["left"] == 7000 - st["used"] == r["budget_left"]
    ids = set()
    for _ in range(30):
        n = run(capsys, work, "index", "next")
        if n["status"] != "unit":
            break
        ids.add(n["unit_id"])
        submit(capsys, work, tmp_path, n["unit_id"], EMPTY)
    assert n["status"] == "budget_exhausted" and run(capsys, work, "index", "status")["units"]["pending"] > 0


def test_three_rejections_mark_the_unit_failed(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    unit = run(capsys, work, "index", "next")["unit_id"]
    r1 = submit(capsys, work, tmp_path, unit, BAD)
    assert r1["status"] == "rejected" and r1["accepted"] == 0 and r1["attempts_left"] == 2
    assert r1["rejected"][0]["item"] == "relations[0]" and "not a table in the map" in r1["rejected"][0]["reason"]
    assert run(capsys, work, "index", "next")["unit_id"] == unit and run(capsys, work, "index", "next")["attempt"] == 2
    # a whole-answer schema failure counts too
    r2 = submit(capsys, work, tmp_path, unit, {"relations": "no"})
    assert r2["status"] == "rejected" and r2["attempts_left"] == 1 and r2["rejected"][0]["item"] == "$"
    r3 = submit(capsys, work, tmp_path, unit, BAD)
    assert r3["status"] == "failed" and r3["attempts_left"] == 0
    st = run(capsys, work, "index", "status")
    assert st["units"]["failed"] == 1 and st["failed_units"][0]["unit_id"] == unit and st["failed_units"][0]["reasons"]
    assert st["accept_rate"] == 0.0
    assert run(capsys, work, "index", "next")["unit_id"] != unit  # the failed unit is not handed out again
    assert not (work / "index" / "answers" / f"{unit}.json").exists()  # it keeps the deterministic map
    f = tmp_path / "answer.json"
    assert main(["index", "submit", unit, str(f), "--dir", str(work)]) == 2  # no resubmitting a failed unit
    assert "already failed" in capsys.readouterr().err


def test_submit_reads_stdin(work, capsys, monkeypatch):
    import io
    run(capsys, work, "index", "start", "--tasks", "relations")
    unit = run(capsys, work, "index", "next")["unit_id"]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(EMPTY)))
    assert run(capsys, work, "index", "submit", unit, "-")["status"] == "accepted"


def test_model_comes_from_the_flag_or_the_json(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    a, b = run(capsys, work, "index", "next")["unit_id"], None
    submit(capsys, work, tmp_path, a, {**EMPTY, "model": "from-json"})
    b = run(capsys, work, "index", "next")["unit_id"]
    submit(capsys, work, tmp_path, b, {**EMPTY, "model": "from-json"}, "--model", "from-flag")
    c = run(capsys, work, "index", "next")["unit_id"]
    submit(capsys, work, tmp_path, c, EMPTY)
    models = {u: json.loads((work / "index" / "answers" / f"{u}.json").read_text())["model"] for u in (a, b, c)}
    assert models == {a: "from-json", b: "from-flag", c: "unknown"}


def test_done_when_every_unit_is_submitted_and_finish_closes_the_job(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations", "--max-units", "2")
    for _ in range(2):
        submit(capsys, work, tmp_path, run(capsys, work, "index", "next")["unit_id"], EMPTY)
    n = run(capsys, work, "index", "next")
    assert n == {"status": "done", "finish": "keygraph index finish"}
    st = run(capsys, work, "index", "status")
    assert st["units"]["skipped"] == 16 and st["units"]["done"] == 2 and st["next"] == "keygraph index finish"
    fin = run(capsys, work, "index", "finish")
    assert fin["units"] == 2 and (work / "index" / "summary.json").exists()
    assert run(capsys, work, "index", "status")["status"] == "done"
    assert main(["index", "start", "--dir", str(work)]) == 2  # a finished job is not restarted by accident


def _units(out: Path) -> dict:
    with ContextDB(out / "keygraph.db") as ctx:
        job = jobs.latest_job(ctx)
        return {u["unit_id"]: u for u in jobs.list_units(ctx, job["job_id"])}


def test_refresh_marks_only_changed_units(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    done = []
    for _ in range(3):
        u = run(capsys, work, "index", "next")["unit_id"]
        submit(capsys, work, tmp_path, u, EMPTY)
        done.append(u)
    assert ORDERS_UNIT in done
    nothing = run(capsys, work, "index", "refresh")
    assert nothing["changed"] == [] and nothing["new"] == [] and nothing["dropped"] == [] and nothing["unchanged"] == 18
    # a real change to the orders note changes that unit's packet only
    assert main(["context", "add", "--kind", "table", "table=shop.orders", "grain=one row per checkout", "--verified",
                 "--dir", str(work)]) == 0
    capsys.readouterr()
    r = run(capsys, work, "index", "refresh")
    assert r["changed"] == [ORDERS_UNIT] and r["new"] == [] and r["dropped"] == [] and r["unchanged"] == 17
    after = _units(work)
    assert after[ORDERS_UNIT]["status"] == "pending" and after[ORDERS_UNIT]["attempts"] == 0
    assert [u for u in done if u != ORDERS_UNIT and after[u]["status"] == "done"] == [u for u in done if u != ORDERS_UNIT]


def test_refresh_adds_new_units_and_drops_removed_ones(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    gone = next(u for u in _units(work) if u != ORDERS_UNIT)
    submit(capsys, work, tmp_path, run(capsys, work, "index", "next")["unit_id"], EMPTY)
    with ContextDB(work / "keygraph.db") as ctx:  # fake a removed table (unit not in the plan) and a table that has no unit yet
        job = jobs.latest_job(ctx)["job_id"]
        ctx.db.execute("UPDATE index_units SET unit_id='ffffffffffff', status='done' WHERE job_id=? AND unit_id=?", (job, gone))
        ctx.db.commit()
    stale = work / "index" / "answers" / "ffffffffffff.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}", encoding="utf-8")
    r = run(capsys, work, "index", "refresh")
    assert r["new"] == [gone] and r["dropped"] == ["ffffffffffff"] and r["changed"] == []
    assert not stale.exists() and _units(work)[gone]["status"] == "pending"


def test_refresh_reopens_a_finished_job_and_needs_a_job(work, capsys, tmp_path):
    assert main(["index", "refresh", "--dir", str(work)]) == 2
    run(capsys, work, "index", "start", "--tasks", "relations", "--max-units", "1")
    submit(capsys, work, tmp_path, run(capsys, work, "index", "next")["unit_id"], EMPTY)
    run(capsys, work, "index", "finish")
    assert run(capsys, work, "index", "status")["status"] == "done"
    main(["context", "add", "--kind", "table", "table=shop.orders", "grain=one row per checkout", "--verified", "--dir", str(work)])
    capsys.readouterr()
    r = run(capsys, work, "index", "refresh")
    assert r["changed"] == [ORDERS_UNIT] and run(capsys, work, "index", "status")["status"] == "open"


def test_submit_errors_are_clear(work, capsys, tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps(EMPTY), encoding="utf-8")
    assert main(["index", "submit", "abc", str(f), "--dir", str(work)]) == 2  # no job yet
    assert "index start" in capsys.readouterr().err
    run(capsys, work, "index", "start", "--tasks", "relations")
    assert main(["index", "submit", "abc", str(f), "--dir", str(work)]) == 2
    assert "not in job-0001" in capsys.readouterr().err
    assert main(["index", "start", "--tasks", "bogus", "--dir", str(work), "--restart"]) == 2


def test_text_output_without_json(work, capsys):
    assert main(["index", "start", "--dir", str(work)]) == 0
    assert "started job-0001" in capsys.readouterr().out
    assert main(["index", "next", "--dir", str(work)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("unit ") and "answer schema" in out
    assert main(["index", "status", "--dir", str(work)]) == 0
    assert "accept rate n/a" in capsys.readouterr().out


def test_devlog_policy_for_index_calls_keeps_counts_not_text():
    from keygraph import devlog
    resp = {"status": "rejected", "accepted": 1, "rejected": [{"item": "relations[0]", "reason": "email jo@x.com"}],
            "packet": {"profiles": {"a.b": {"numeric": {"min": 5551234567}}}}, "model": "secret-model", "probe_source": "url"}
    shaped = devlog.response_fields("index submit", resp)["response"]
    assert shaped == {"status": "rejected", "accepted": 1, "rejected": {"len": 1}, "packet": {"bytes": shaped["packet"]["bytes"]},
                      "model": "<text>", "probe_source": "url"}
    assert devlog.index_args({"answer": {"relations": [1, 2], "model": "x"}, "unit_id": "u"}) == \
        {"answer": {"relations": 2, "model": "<text>"}, "unit_id": "u"}
    assert devlog.is_index_tool("index_submit") and devlog.is_index_tool("index next") and not devlog.is_index_tool("search")


def test_start_remembers_names_and_dialect_only(work, capsys, monkeypatch):
    monkeypatch.setenv("KG_PROBE_CMD", "mysql -pSECRET -e {sql}")
    r = run(capsys, work, "index", "start", "--cmd-env", "KG_PROBE_CMD", "--dialect", "mysql")
    assert r["cmd_env"] == "KG_PROBE_CMD" and r["dialect"] == "mysql" and "SECRET" not in json.dumps(r)
    with ContextDB(work / "keygraph.db") as ctx:
        row = ctx.db.execute("SELECT * FROM index_jobs").fetchone()
        assert "SECRET" not in json.dumps(list(row))
    with pytest.raises(SystemExit):  # the two flags exclude each other
        main(["index", "start", "--url-env", "A", "--cmd-env", "B", "--dir", str(work)])


# --- round 1 fixes -----------------------------------------------------------------------------------------------

def _dev_log(out: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in (out / "dev").glob("events*.jsonl"))


def test_cmd_env_and_url_env_values_never_reach_the_dev_log(work, capsys, monkeypatch):
    monkeypatch.setenv("KEYGRAPH_DEV", "1")
    for flag in ("--cmd-env", "--url-env"):
        assert main(["index", "start", flag, "mysql -phunter3 {sql}", "--dir", str(work)]) == 2
    monkeypatch.delenv("KG_NOT_SET_URL", raising=False)
    assert main(["index", "start", "--url-env", "KG_NOT_SET_URL", "--dir", str(work)]) == 2
    err = capsys.readouterr().err
    log = _dev_log(work)
    assert "hunter3" not in log and "hunter3" not in err and "<invalid>" in log and "KG_NOT_SET_URL" in log


TASKS2 = ("--tasks", "tables,columns")
T_ORDERS = {"table": "shop.orders", "purpose": "One checkout", "reason": "comment", "confidence": 0.8}
T_CUST = {"table": "shop.customers", "purpose": "A buyer", "reason": "comment", "confidence": 0.8}
T_BAD = {"table": "nope.nothing", "purpose": "x", "reason": "made up", "confidence": 0.5}
RESUBMIT = "resubmit only the rejected items for this unit_id; accepted items are already stored"


def _answer_file(out: Path, unit: str) -> dict:
    return json.loads((out / "index" / "answers" / f"{unit}.json").read_text(encoding="utf-8"))


def test_partial_answer_keeps_the_unit_pending_and_resubmit_merges(work, capsys, tmp_path):
    run(capsys, work, "index", "start", *TASKS2)
    assert run(capsys, work, "index", "next")["unit_id"] == ORDERS_UNIT
    r1 = submit(capsys, work, tmp_path, ORDERS_UNIT, {"tables": [T_ORDERS, T_BAD], "columns": []})
    assert r1["status"] == "partially_accepted" and r1["resubmit"] is True and r1["accepted"] == 1 and r1["attempts_left"] == 2
    assert [x["item"] for x in r1["rejected"]] == ["tables[1]"] and RESUBMIT in r1["hint"]
    assert ORDERS_UNIT in r1["next"] and "submit" in r1["next"]
    n = run(capsys, work, "index", "next")
    assert n["unit_id"] == ORDERS_UNIT and n["attempt"] == 2  # still pending
    assert _answer_file(work, ORDERS_UNIT)["answer"]["tables"][0]["table"] == "shop.orders"
    # the resubmit holds only the corrected item, and no `columns` list at all
    r2 = submit(capsys, work, tmp_path, ORDERS_UNIT, {"tables": [T_CUST, T_ORDERS]})
    assert r2["status"] == "accepted" and r2["rejected"] == [] and "attempts_left" not in r2
    assert r2["next"] == "keygraph index next"
    assert [t["table"] for t in _answer_file(work, ORDERS_UNIT)["answer"]["tables"]] == ["shop.orders", "shop.customers"]  # union, no duplicate
    assert _units(work)[ORDERS_UNIT]["status"] == "done"


def test_partial_answers_fail_the_unit_after_the_third_submission(work, capsys, tmp_path):
    run(capsys, work, "index", "start", *TASKS2)
    left = []
    for _ in range(3):
        r = submit(capsys, work, tmp_path, ORDERS_UNIT, {"tables": [T_ORDERS, T_BAD], "columns": []})
        left.append((r["status"], r.get("attempts_left")))
    assert left == [("partially_accepted", 2), ("partially_accepted", 1), ("failed", 0)]
    assert r["next"] == "keygraph index next" and _units(work)[ORDERS_UNIT]["status"] == "failed"
    assert [t["table"] for t in _answer_file(work, ORDERS_UNIT)["answer"]["tables"]] == ["shop.orders"]  # accepted items stay


def test_every_submit_reply_carries_next_and_accepted_has_no_attempts_left(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    rej = submit(capsys, work, tmp_path, ORDERS_UNIT, BAD)
    assert rej["status"] == "rejected" and rej["resubmit"] is True and ORDERS_UNIT in rej["next"] and rej["attempts_left"] == 2
    ok = submit(capsys, work, tmp_path, ORDERS_UNIT, EMPTY)
    assert ok["status"] == "accepted" and ok["next"] == "keygraph index next" and "attempts_left" not in ok
    unit = run(capsys, work, "index", "next")["unit_id"]
    for _ in range(3):
        last = submit(capsys, work, tmp_path, unit, BAD)
    assert last["status"] == "failed" and last["next"] == "keygraph index next" and last["attempts_left"] == 0


def test_missing_kinds_are_one_rejection_with_a_fix(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations,columns,tables")
    r = submit(capsys, work, tmp_path, ORDERS_UNIT, {"relations": []})
    assert [x["item"] for x in r["rejected"]] == ["$"]
    assert r["rejected"][0]["reason"] == "missing `columns`, `tables`: include each as [] if you have nothing to add"
    r = submit(capsys, work, tmp_path, ORDERS_UNIT, {"relations": [], "columns": []})
    assert r["rejected"][0]["reason"] == "missing `tables`: include it as [] if you have nothing to add"
    r = submit(capsys, work, tmp_path, ORDERS_UNIT, {"relations": "no", "columns": [], "tables": []})
    assert "expected array (a JSON list" in r["rejected"][0]["reason"]


def test_instructions_header_has_naming_rule_and_limits_for_every_kind():
    from keygraph.index import schema as SC
    for kind in SC.TASKS:
        text = SC.instructions([kind])
        assert "schema.table" in text.split("\n")[0] and "exactly as in the packet" in text.split("\n")[0]
        assert "240" in text.split("\n")[0] and "400" in text.split("\n")[0]
        assert "prompt hash" not in text


def test_unreadable_file_and_invalid_json_exit_2_without_a_traceback(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    for name, data in (("missing.json", None), ("binary.json", b"\xff\xfe\x00"), ("bad.json", b"{not json")):
        f = tmp_path / name
        if data is not None:
            f.write_bytes(data)
        assert main(["index", "submit", ORDERS_UNIT, str(f), "--dir", str(work)]) == 2
        err = capsys.readouterr().err
        assert err.startswith("keygraph: ") and "Traceback" not in err
        assert main(["index", "submit", ORDERS_UNIT, str(f), "--dir", str(work), "--json"]) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error" and out["error"]
    assert _units(work)[ORDERS_UNIT]["attempts"] == 0  # none of them used an attempt


def test_submit_and_next_on_a_finished_job_say_what_to_do(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations", "--max-units", "1")
    submit(capsys, work, tmp_path, ORDERS_UNIT, EMPTY)
    run(capsys, work, "index", "finish")
    hint = ("the job is finished; run `keygraph index refresh` to index changed tables or `keygraph index start --restart` to redo all")
    assert submit(capsys, work, tmp_path, ORDERS_UNIT, EMPTY) == {"status": "finished", "hint": hint}
    assert run(capsys, work, "index", "next") == {"status": "finished", "hint": hint}


def test_refresh_also_reopens_failed_units(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations", "--max-units", "1")
    for _ in range(3):
        submit(capsys, work, tmp_path, ORDERS_UNIT, BAD)
    run(capsys, work, "index", "finish")
    r = run(capsys, work, "index", "refresh")
    assert r["retried"] == [ORDERS_UNIT] and r["changed"] == [] and r["pending"] == 1
    assert _units(work)[ORDERS_UNIT]["status"] == "pending" and _units(work)[ORDERS_UNIT]["attempts"] == 0
    assert run(capsys, work, "index", "status")["status"] == "open"


def test_packet_is_charged_once_per_unit(work, capsys, tmp_path):
    run(capsys, work, "index", "start", "--tasks", "relations")
    first = submit(capsys, work, tmp_path, ORDERS_UNIT, BAD)
    second = submit(capsys, work, tmp_path, ORDERS_UNIT, BAD)
    est = _units(work)[ORDERS_UNIT]["tokens_est"]
    cost1 = 200000 - first["budget_left"]
    cost2 = first["budget_left"] - second["budget_left"]
    assert cost1 >= est and cost2 < est  # the second submit paid for the answer only


def test_budget_exhausted_names_the_unit_it_could_not_afford(work, capsys):
    run(capsys, work, "index", "start", "--budget", "100")
    assert run(capsys, work, "index", "next")["unit_id"] == ORDERS_UNIT


def test_answer_file_is_written_only_after_the_unit_is_updated(work, capsys, tmp_path, monkeypatch):
    """A crash between the facts commit and the unit update leaves no answer file: replay never sees a unit that is not done."""
    from keygraph.index import cli_api as X
    run(capsys, work, "index", "start", "--tasks", "relations")
    real = X.jobs.update_unit

    def boom(*a, **k):
        if k.get("status") == "done":
            raise RuntimeError("crash")
        return real(*a, **k)

    monkeypatch.setattr(X.jobs, "update_unit", boom)
    with pytest.raises(RuntimeError):
        X.submit_text(work, ORDERS_UNIT, json.dumps(EMPTY))
    assert not (work / "index" / "answers" / f"{ORDERS_UNIT}.json").exists()
    monkeypatch.undo()
    assert submit(capsys, work, tmp_path, ORDERS_UNIT, EMPTY)["status"] == "accepted"  # resubmit heals it
    assert not list((work / "index" / "answers").glob("*.tmp"))
