import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.jobs import _snapshot_for_rerun, control_job


def _job(**kwargs):
    base = dict(
        id=uuid.uuid4(),
        status="failed",
        run_attempt=1,
        params_snapshot={"start_stage": "S0_io", "output_dir": "/data/out"},
        completed_stage="S1_catalog",
        started_at=None,
        finished_at="x",
        log_dir=None,
        output_dir="/tmp/out",
        message="boom",
        error_summary="File is not a zip file",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _db(job):
    db = MagicMock()
    db.get.return_value = job
    db.scalar.return_value = None
    return db


def test_snapshot_for_rerun_starts_after_completed():
    job = _job(completed_stage="S2_at")
    snap = _snapshot_for_rerun(job)
    assert snap["start_stage"] == "S3_dense"


def test_snapshot_for_rerun_bumps_to_s2_when_at_checkpoint_exists(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "at_result.npz").write_bytes(b"npz")
    job = _job(
        completed_stage="S1_catalog",
        params_snapshot={"start_stage": "S0_io", "output_dir": str(tmp_path), "cache_dir": str(cache / "features")},
    )
    snap = _snapshot_for_rerun(job)
    assert snap["start_stage"] == "S3_dense"


def test_snapshot_for_rerun_reuses_existing_dsm(tmp_path):
    products = tmp_path / "拼图结果"
    products.mkdir()
    (products / "DSM.tif").write_bytes(b"dsm")
    job = _job(
        completed_stage="S2_at",
        params_snapshot={"start_stage": "S0_io", "output_dir": str(tmp_path), "products_dir": str(products)},
    )
    snap = _snapshot_for_rerun(job)
    assert snap["start_stage"] == "S5_ortho"
    assert snap["reuse_dsm"] == str(products / "DSM.tif")


def test_retry_failed_reuses_output_and_publishes():
    job = _job()
    mq = MagicMock()
    control_job(_db(job), mq, job.id, "retry")
    assert job.status == "running"
    assert job.run_attempt == 2
    assert job.error_summary is None
    assert job.finished_at is None
    payload = mq.publish_job.call_args[0][0]
    assert payload["action"] == "retry"
    assert payload["params_snapshot"]["start_stage"] == "S2_at"
    assert payload["params_snapshot"]["output_dir"] == "/data/out"


def test_retry_rejects_running():
    job = _job(status="running")
    with pytest.raises(HTTPException) as ei:
        control_job(_db(job), MagicMock(), job.id, "retry")
    assert ei.value.status_code == 400
    assert "失败" in str(ei.value.detail)


def test_recover_stale_running_reuses_dsm(tmp_path, monkeypatch):
    products = tmp_path / "拼图结果"
    products.mkdir()
    (products / "DSM.tif").write_bytes(b"dsm")
    job = _job(
        status="running",
        completed_stage="S2_at",
        params_snapshot={"start_stage": "S0_io", "output_dir": str(tmp_path), "products_dir": str(products)},
        log_dir=str(tmp_path / "log"),
    )
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: False)
    mq = MagicMock()
    control_job(_db(job), mq, job.id, "recover")
    assert job.status == "running"
    assert job.completed_stage == "S4_dsm"
    payload = mq.publish_job.call_args[0][0]
    assert payload["action"] == "recover"
    assert payload["params_snapshot"]["start_stage"] == "S5_ortho"
    assert payload["params_snapshot"]["reuse_dsm"] == str(products / "DSM.tif")
    mq.publish_control.assert_called()
    mq.purge_jobs.assert_called()


def test_recover_blocked_when_wrapper_still_holds(monkeypatch):
    job = _job(status="running")
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: True)
    with pytest.raises(HTTPException) as ei:
        control_job(_db(job), MagicMock(), job.id, "recover")
    assert ei.value.status_code == 400
    assert "暂停" in str(ei.value.detail)


def test_pause_only_running():
    job = _job(status="failed")
    with pytest.raises(HTTPException) as ei:
        control_job(_db(job), MagicMock(), job.id, "pause")
    assert ei.value.status_code == 400
    job.status = "running"
    mq = MagicMock()
    control_job(_db(job), mq, job.id, "pause")
    assert "暂停" in job.message
    mq.publish_control.assert_called_once()


def test_pause_live_keeps_running_until_checkpoint(monkeypatch):
    job = _job(status="running")
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: True)
    control_job(_db(job), MagicMock(), job.id, "pause")
    assert job.status == "running"
    assert "阶段结束后生效" in job.message


def test_stop_kills_process_and_unlocks_page(monkeypatch):
    job = _job(status="running", completed_stage="S4_dsm")
    mq = MagicMock()
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: True)
    control_job(_db(job), mq, job.id, "stop")
    assert job.status == "paused"
    assert "已立即停止" in job.message
    sent = mq.publish_control.call_args[0][0]
    assert sent["action"] == "kill"
    assert sent["job_id"] == str(job.id)


def test_pause_orphan_sets_paused_immediately(monkeypatch):
    job = _job(status="running")
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: False)
    control_job(_db(job), MagicMock(), job.id, "pause")
    assert job.status == "paused"
    assert "立即暂停" in job.message


def test_resume_when_worker_holds_does_not_republish(monkeypatch):
    job = _job(status="paused", completed_stage="S4_dsm")
    mq = MagicMock()
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: True)
    control_job(_db(job), mq, job.id, "resume")
    assert job.status == "running"
    assert job.run_attempt == 1
    mq.publish_job.assert_not_called()
    mq.publish_control.assert_called()


def test_resume_when_worker_gone_republishes_next_stage(monkeypatch):
    job = _job(status="paused", completed_stage="S4_dsm")
    mq = MagicMock()
    monkeypatch.setattr("app.services.jobs.heartbeat_holds_job", lambda *_a, **_k: False)
    control_job(_db(job), mq, job.id, "resume")
    assert job.status == "running"
    payload = mq.publish_job.call_args[0][0]
    assert payload["action"] == "resume"
    assert payload["params_snapshot"]["start_stage"] == "S5_ortho"
