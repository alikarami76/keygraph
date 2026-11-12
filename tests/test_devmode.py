import json

import pytest

from keygraph import devlog
from keygraph.cli import main
from tests.test_example_shop import _build


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("KEYGRAPH_DEV", raising=False)
    monkeypatch.delenv("KEYGRAPH_DEV_SESSION", raising=False)


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    out = tmp_path_factory.mktemp("devmode_shop")
    _build(out)
    return out


def events(out):
    p = out / "dev" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_off_by_default_writes_nothing(shop, capsys):
    assert not (shop / "dev").exists() or not events(shop)
    assert main(["path", "customers", "shipments", "--dir", str(shop)]) == 0
    assert not events(shop) and not (shop / "dev" / "traces").exists()


def test_dev_on_logs_a_command_event(tmp_path, capsys, monkeypatch):
    _build(tmp_path)
    monkeypatch.setenv("KEYGRAPH_DEV_SESSION", "s1")
    assert main(["dev", "on", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["path", "customers", "shipments", "--dir", str(tmp_path), "--json"]) == 0
    printed = capsys.readouterr().out
    (ev,) = events(tmp_path)
    assert (ev["v"], ev["kind"], ev["tool"], ev["ok"], ev["session"], ev["surface"]) == (1, "command", "path", True, "s1", "cli")
    assert ev["ms"] >= 0 and ev["args"]["json"] is True and "fn" not in ev["args"] and "dir" not in ev["args"]
    assert ev["response"] == json.loads(printed)


def test_failing_command_logs_error_and_keeps_exit_code(tmp_path, capsys):
    _build(tmp_path)
    main(["dev", "on", "--dir", str(tmp_path)])
    assert main(["path", "no_such_table", "customers", "--dir", str(tmp_path)]) == 2
    (ev,) = events(tmp_path)
    assert ev["ok"] is False and ev["error"]["type"] == "UnknownTable" and ev["args"]["exit_status"] == 2


def test_env_overrides_marker_and_forces_on(tmp_path, monkeypatch):
    main(["dev", "on", "--dir", str(tmp_path)])
    assert devlog.enabled(tmp_path)
    monkeypatch.setenv("KEYGRAPH_DEV", "0")
    assert not devlog.enabled(tmp_path) and devlog.status(tmp_path)["source"] == "off"
    monkeypatch.setenv("KEYGRAPH_DEV", "yes")
    (tmp_path / "dev" / "enabled").unlink()
    assert devlog.enabled(tmp_path) and devlog.status(tmp_path)["source"] == "env"


def test_arg_masking():
    m = devlog.mask_args({"url": "mysql://u:secret@h/db", "sql": "select * from t where a = 'bob' and b = 42",
                          "list": ["postgres://x:y@z/q", "token=abcdefghijklmnop1234567890"], "n": 3})
    assert m["url"] == "mysql://***" and "secret" not in json.dumps(m)
    assert "bob" not in m["sql"] and "42" not in m["sql"] and m["n"] == 3 and m["list"][0] == "postgres://***"
    assert "secret" not in devlog.mask_text("failed mysql://u:secret@h/db for a@b.com") and "a@b.com" not in devlog.mask_text("a@b.com")


def test_scan_url_is_redacted_in_the_log(tmp_path, monkeypatch):
    main(["dev", "on", "--dir", str(tmp_path)])
    with pytest.raises((Exception, SystemExit)):  # no driver or no server: either way the command fails and is logged
        main(["scan", "--url", "mysql://u:secret@127.0.0.1:1/db", "--dir", str(tmp_path)])
    text = (tmp_path / "dev" / "events.jsonl").read_text()
    assert "secret" not in text and "mysql://***" in text


def test_rotation_keeps_numbered_files(tmp_path, monkeypatch):
    monkeypatch.setattr(devlog, "MAX_BYTES", 300)
    r = devlog.Recorder(tmp_path)
    for i in range(30):
        r.record("note", "dev_note", {"i": i, "pad": "x" * 50})
    names = sorted(p.name for p in (tmp_path / "dev").glob("events*.jsonl"))
    assert "events.1.jsonl" in names and "events.5.jsonl" in names and "events.6.jsonl" not in names


def test_tracer_cap_writes_one_truncated_event(tmp_path, monkeypatch):
    monkeypatch.setattr(devlog, "TRACE_MAX_EVENTS", 5)
    main(["dev", "on", "--dir", str(tmp_path)])
    assert not devlog.active()
    with devlog.tracing(tmp_path, "build") as path:
        assert devlog.active()
        for i in range(20):
            devlog.trace("cand", i=i)
    assert not devlog.active()
    kinds = [json.loads(x)["kind"] for x in path.read_text().splitlines()]
    assert kinds == ["cand"] * 5 + ["truncated"]


def test_trace_is_a_noop_when_dev_is_off(tmp_path):
    with devlog.tracing(tmp_path, "build") as path:
        devlog.trace("x")
        assert path is None and not devlog.active()
    assert not (tmp_path / "dev").exists()


def test_old_traces_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(devlog, "KEEP_TRACES", 3)
    main(["dev", "on", "--dir", str(tmp_path)])
    for i in range(6):
        with devlog.tracing(tmp_path, f"r{i}"):
            devlog.trace("x")
    assert len(list((tmp_path / "dev" / "traces").glob("*.jsonl"))) == 3
    assert devlog.status(tmp_path)["trace_files"] == 3


def test_decisions_are_capped_and_scoped():
    devlog.decide("ignored")
    with devlog.collecting() as d:
        for i in range(300):
            devlog.decide("rule", i=i)
    assert len(d) == devlog.DECISIONS_MAX


def test_a_failing_recorder_does_not_change_output_or_exit_code(tmp_path, capsys, monkeypatch):
    _build(tmp_path)
    capsys.readouterr()
    main(["path", "customers", "shipments", "--dir", str(tmp_path)])
    plain = capsys.readouterr().out
    main(["dev", "on", "--dir", str(tmp_path)])
    capsys.readouterr()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(devlog.json, "dumps", boom)  # breaks the recorder only if it reaches it
    monkeypatch.setattr(devlog.Recorder, "_rotate", boom)
    assert main(["path", "customers", "shipments", "--dir", str(tmp_path)]) == 0
    cap = capsys.readouterr()
    assert cap.out == plain


def test_a_failing_recorder_keeps_error_exit_code(tmp_path, monkeypatch):
    _build(tmp_path)
    main(["dev", "on", "--dir", str(tmp_path)])
    monkeypatch.setattr(devlog.Recorder, "_rotate", lambda self: (_ for _ in ()).throw(OSError("x")))
    assert main(["path", "nope", "customers", "--dir", str(tmp_path)]) == 2


def test_dev_note_json_and_when_off(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("KEYGRAPH_DEV_SESSION", "s9")
    assert main(["dev", "note", "how do orders reach shipments? mail a@b.com", "--dir", str(tmp_path), "--json"]) == 0
    off = json.loads(capsys.readouterr().out)
    assert off["noted"] is False and "a@b.com" not in off["question"] and not events(tmp_path)
    main(["dev", "on", "--dir", str(tmp_path)])
    capsys.readouterr()
    assert main(["dev", "note", "orders", "to", "shipments", "--dir", str(tmp_path), "--json"]) == 0
    on = json.loads(capsys.readouterr().out)
    assert on == {"noted": True, "session": "s9", "question": "orders to shipments"}
    (ev,) = events(tmp_path)
    assert ev["kind"] == "note" and ev["args"]["question"] == "orders to shipments" and ev["session"] == "s9"


def test_dev_status_and_clear(tmp_path, capsys):
    main(["dev", "status", "--dir", str(tmp_path), "--json"])
    assert json.loads(capsys.readouterr().out) == {"enabled": False, "source": "off", "dir": str(tmp_path / "dev"),
                                                   "events_bytes": 0, "trace_files": 0}
    main(["dev", "on", "--dir", str(tmp_path)])
    devlog.Recorder(tmp_path).record("note", "dev_note", {"q": 1})
    with devlog.tracing(tmp_path, "t"):
        devlog.trace("x")
    capsys.readouterr()
    main(["dev", "status", "--dir", str(tmp_path), "--json"])
    st = json.loads(capsys.readouterr().out)
    assert st["enabled"] and st["source"] == "marker" and st["events_bytes"] > 0 and st["trace_files"] == 1
    main(["dev", "clear", "--dir", str(tmp_path)])
    assert devlog.enabled(tmp_path) and not events(tmp_path) and devlog.status(tmp_path)["trace_files"] == 0
    main(["dev", "clear", "--all", "--dir", str(tmp_path)])
    assert not devlog.enabled(tmp_path)
    main(["dev", "off", "--dir", str(tmp_path)])


def test_mask_response_masks_profile_values_nested():
    prof = {"null_rate": 0.1, "length": {"min": 1, "max": 9}, "top_values": [["a@b.com", 3]], "numeric": {"min": 1, "max": 2},
            "dates": {"min": "2020-01-01", "recency_days": 3.0}, "distinct_in_sample": 4}
    r = devlog.mask_response("describe", {"table": "t", "columns": [{"name": "c", "profile": prof}]})
    p = r["columns"][0]["profile"]
    assert p["top_values"] == p["numeric"] == p["dates"] == "<masked>"
    assert p["length"] == {"min": 1, "max": 9} and p["null_rate"] == 0.1 and "a@b.com" not in json.dumps(r)


def test_response_policy_inline_vs_ref():
    small = devlog.response_fields("search", {"tables": [{"table": "a"}]})
    assert small == {"response": {"tables": [{"table": "a"}]}}
    big = devlog.response_fields("search", {"tables": [{"table": f"t{i}", "pad": "x" * 100} for i in range(300)]})
    ref = big["response_ref"]
    assert "response" not in big and ref["bytes"] > 16 * 1024 and ref["tokens_approx"] > 4000
    assert len(ref["sha256"]) == 64 and ref["shape"]["tables"][0] == "t0" and ref["shape"]["lists"] == {"tables": 300}
    again = devlog.response_fields("search", {"tables": [{"table": f"t{i}", "pad": "x" * 100} for i in range(300)]})
    assert again == big
    p = devlog.response_fields("path", {"paths": [{"score": 2.5, "pad": "x" * 20000}, {"score": 1.0}]})
    assert p["response_ref"]["shape"]["paths"] == 2 and p["response_ref"]["shape"]["top_score"] == 2.5


def test_large_stdout_becomes_a_ref(tmp_path, capsys, monkeypatch):
    _build(tmp_path)
    main(["dev", "on", "--dir", str(tmp_path)])
    capsys.readouterr()
    monkeypatch.setattr(devlog, "INLINE_MAX", 50)
    assert main(["path", "customers", "shipments", "--dir", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    (ev,) = events(tmp_path)
    assert "response" not in ev and ev["response_ref"]["bytes"] == len(printed.encode())


def test_doctor_reports_dev_mode(tmp_path):
    from keygraph import doctor as D
    c = next(c for c in D.run(tmp_path)["checks"] if c["name"] == "dev_mode")
    assert c["status"] == "ok" and c["detail"] == "off"
    main(["dev", "on", "--dir", str(tmp_path)])
    c = next(c for c in D.run(tmp_path)["checks"] if c["name"] == "dev_mode")
    assert c["status"] == "ok" and c["detail"].startswith("on: logging to ") and c["detail"].endswith("KB)")
