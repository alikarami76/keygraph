import pytest

from keygraph.context import ContextDB
from keygraph.index import jobs as J

UNITS = [{"unit_id": "aaa", "tables": ["app.a", "app.b"], "rank": 0}, {"unit_id": "bbb", "tables": ["app.c"], "rank": 1}]


def test_create_and_roundtrip(tmp_path):
    with ContextDB(tmp_path / "k.db") as ctx:
        jid = J.create_job(ctx, UNITS, budget=5000, tasks=["relations", "columns"], url_env="SHOP_URL")
        j = J.get_job(ctx, jid)
        assert j["budget"] == 5000 and j["used"] == 0 and j["tasks"] == ["relations", "columns"]
        assert j["url_env"] == "SHOP_URL" and j["cmd_env"] == "" and j["status"] == "open"
        assert j["keygraph_version"] and j["schema_version"] == "1"
        us = J.list_units(ctx, jid)
        assert [(u["unit_id"], u["status"], u["attempts"], u["tables"]) for u in us] == [
            ("aaa", "pending", 0, ["app.a", "app.b"]), ("bbb", "pending", 0, ["app.c"])]


def test_only_env_names_are_stored(tmp_path):
    with ContextDB(tmp_path / "k.db") as ctx:
        with pytest.raises(ValueError, match="NAME"):
            J.create_job(ctx, UNITS, budget=1, url_env="postgresql://u:secret@h/db")
        with pytest.raises(ValueError, match="NAME"):
            J.create_job(ctx, UNITS, budget=1, cmd_env="mysql -e {sql}")
        with pytest.raises(ValueError, match="not both"):
            J.create_job(ctx, UNITS, budget=1, url_env="A", cmd_env="B")
        J.create_job(ctx, UNITS, budget=1, cmd_env="PROBE_CMD")
        assert J.open_job(ctx)["cmd_env"] == "PROBE_CMD"


def test_unknown_task_rejected(tmp_path):
    with ContextDB(tmp_path / "k.db") as ctx, pytest.raises(ValueError, match="unknown task"):
        J.create_job(ctx, UNITS, budget=1, tasks=["nope"])


def test_resume_finds_open_job_across_reopen(tmp_path):
    path = tmp_path / "k.db"
    with ContextDB(path) as ctx:
        assert J.open_job(ctx) is None
        jid = J.create_job(ctx, UNITS, budget=100)
        J.update_unit(ctx, jid, "aaa", status="done", attempts=1, tokens_est=300, packet_hash="h1")
    with ContextDB(path) as ctx:
        j = J.open_job(ctx)
        assert j["job_id"] == jid
        assert J.next_unit(ctx, jid)["unit_id"] == "bbb"
        u = J.get_unit(ctx, jid, "aaa")
        assert (u["status"], u["attempts"], u["tokens_est"], u["packet_hash"]) == ("done", 1, 300, "h1")
        J.close_job(ctx, jid)
        assert J.open_job(ctx) is None
        assert J.create_job(ctx, UNITS, budget=1) == "job-0002"


def test_update_validates(tmp_path):
    with ContextDB(tmp_path / "k.db") as ctx:
        jid = J.create_job(ctx, UNITS, budget=1)
        with pytest.raises(ValueError):
            J.update_unit(ctx, jid, "aaa", status="weird")
        with pytest.raises(KeyError):
            J.update_unit(ctx, jid, "zzz", status="done")
        assert J.update_unit(ctx, jid, "aaa", attempts=2)["status"] == "pending"
        assert [u["unit_id"] for u in J.list_units(ctx, jid, "pending")] == ["aaa", "bbb"]


def test_budget_and_progress_have_no_timestamps(tmp_path):
    with ContextDB(tmp_path / "k.db") as ctx:
        jid = J.create_job(ctx, UNITS, budget=1000)
        assert J.add_used(ctx, jid, 400) == 400 and J.add_used(ctx, jid, 700) == 1100
        assert J.budget_left(ctx, jid) == 0
        J.update_unit(ctx, jid, "aaa", status="failed")
        p = J.progress(ctx, jid)
        assert p == {"job_id": jid, "status": "open", "units": {"pending": 1, "done": 0, "failed": 1, "skipped": 0},
                     "budget": 1000, "used": 1100, "left": 0}
        assert not {"started_at", "finished_at", "submitted_at"} & (set(J.get_job(ctx, jid)) | set(J.get_unit(ctx, jid, "aaa")))
