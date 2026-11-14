import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.jobs import delete_all_jobs, delete_job, job_deletable, kill_all_active


def test_running_must_pause_before_delete():
    ok, reason = job_deletable("running")
    assert ok is False
    assert "暂停" in reason


@pytest.mark.parametrize(
    "status",
    ["paused", "queued", "awaiting_continue", "succeeded", "failed", "cancelled"],
)
def test_non_running_can_delete(status):
    ok, reason = job_deletable(status)
    assert ok is True
    assert reason == ""


def test_delete_job_rejects_running():
    job = SimpleNamespace(id=uuid.uuid4(), status="running")
    db = MagicMock()
    db.get.return_value = job
    mq = MagicMock()
    with pytest.raises(HTTPException) as ei:
        delete_job(db, mq, job.id)
    assert ei.value.status_code == 400
    assert "暂停" in str(ei.value.detail)
    db.delete.assert_not_called()
    mq.publish_control.assert_not_called()


def test_delete_all_jobs_cancels_running(tmp_path):
    running = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        log_dir=str(tmp_path / "a" / "log"),
        output_dir=str(tmp_path / "a"),
    )
    done = SimpleNamespace(
        id=uuid.uuid4(),
        status="succeeded",
        log_dir=str(tmp_path / "b" / "log"),
        output_dir=str(tmp_path / "b"),
    )
    db = MagicMock()
    db.scalars.return_value = [running, done]
    mq = MagicMock()
    n = delete_all_jobs(db, mq)
    assert n == 2
    assert db.delete.call_count == 2
    db.commit.assert_called()
    mq.purge_jobs.assert_called_once()
    actions = [c.args[0]["action"] for c in mq.publish_control.call_args_list]
    assert "kill" in actions
    assert "kill_all" in actions
    ctrl = json.loads((tmp_path / "a" / "log" / "control.json").read_text(encoding="utf-8"))
    assert ctrl["cancel"] is True


def test_delete_job_paused_writes_cancel(tmp_path):
    job_id = uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        status="paused",
        log_dir=str(tmp_path / "log"),
        output_dir=str(tmp_path),
    )
    db = MagicMock()
    db.get.return_value = job
    mq = MagicMock()
    delete_job(db, mq, job_id)
    db.delete.assert_called_once_with(job)
    db.commit.assert_called()
    mq.publish_control.assert_called_once()
    payload = mq.publish_control.call_args[0][0]
    assert payload["action"] == "kill"
    assert payload["job_id"] == str(job_id)
    ctrl = json.loads((tmp_path / "log" / "control.json").read_text(encoding="utf-8"))
    assert ctrl["cancel"] is True
    assert ctrl["pause"] is True


def test_owns_record_separates_users():
    from app.auth import owns_record

    user = SimpleNamespace(id=7, roles=[SimpleNamespace(name="executor")])
    admin = SimpleNamespace(id=1, roles=[SimpleNamespace(name="admin")])
    assert owns_record(user, 7) is True
    assert owns_record(user, 1) is False
    assert owns_record(user, None) is False
    assert owns_record(user, None, shared_if_unset=True) is True
    assert owns_record(admin, 1) is True
    assert owns_record(admin, None) is True


def test_delete_all_jobs_owner_does_not_touch_others(tmp_path):
    mine = SimpleNamespace(
        id=uuid.uuid4(),
        status="succeeded",
        created_by=7,
        log_dir=str(tmp_path / "a" / "log"),
        output_dir=str(tmp_path / "a"),
    )
    other = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        created_by=1,
        log_dir=str(tmp_path / "b" / "log"),
        output_dir=str(tmp_path / "b"),
    )
    db = MagicMock()
    db.scalars.return_value = [mine, other]
    mq = MagicMock()
    n = delete_all_jobs(db, mq, owner_id=7)
    assert n == 1
    db.delete.assert_called_once_with(mine)
    mq.purge_jobs.assert_not_called()
    mq.publish_control.assert_not_called()


def test_kill_all_active_owner_skips_foreign_job(tmp_path):
    other = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        created_by=1,
        log_dir=str(tmp_path / "a" / "log"),
        output_dir=str(tmp_path / "a"),
    )
    db = MagicMock()
    db.scalars.return_value = [other]
    mq = MagicMock()
    n = kill_all_active(db, mq, owner_id=7)
    assert n == 0
    assert other.status == "running"
    mq.purge_jobs.assert_not_called()
    mq.publish_control.assert_not_called()


def test_kill_all_active_cancels_live_keeps_succeeded(tmp_path):
    running = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        log_dir=str(tmp_path / "a" / "log"),
        output_dir=str(tmp_path / "a"),
    )
    db = MagicMock()
    db.scalars.return_value = [running]
    mq = MagicMock()
    n = kill_all_active(db, mq)
    assert n == 1
    assert running.status == "cancelled"
    db.delete.assert_not_called()
    db.commit.assert_called()
    mq.purge_jobs.assert_called_once()
    actions = [c.args[0]["action"] for c in mq.publish_control.call_args_list]
    assert "kill" in actions
    assert "kill_all" in actions
