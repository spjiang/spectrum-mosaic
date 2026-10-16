import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.jobs import apply_event, apply_progress, stage_rank


def _job(**kwargs):
    base = dict(
        id=uuid.uuid4(),
        status="running",
        current_stage="S2_at",
        completed_stage="S2_at",
        global_percent=43.0,
        stage_progress=100.0,
        message="",
        eta_seconds=None,
        stage_timings={},
        n_shots=None,
        started_at=None,
        finished_at=None,
        updated_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _db(job):
    db = MagicMock()
    db.get.return_value = job
    return db


def test_stage_rank_order():
    assert stage_rank("S2_at") < stage_rank("S4_dsm")
    assert stage_rank(None) == -1


def test_progress_does_not_rewind_current_stage():
    job = _job(current_stage="S4_dsm", completed_stage="S4_dsm", global_percent=71.0)
    apply_progress(_db(job), {"job_id": str(job.id), "stage_id": "S2_at", "stage_progress": 80, "global_percent": 40})
    assert job.current_stage == "S4_dsm"
    assert job.global_percent == 71.0


def test_stage_done_records_elapsed():
    job = _job(completed_stage="S2_at")
    apply_event(_db(job), {"job_id": str(job.id), "event": "stage_done", "stage_id": "S3_dense", "elapsed_s": 253.2})
    assert job.stage_timings["S3_dense"] == 253.2
    assert job.completed_stage == "S3_dense"


def test_running_event_rewinds_when_stage_restarts():
    job = _job(
        current_stage="S6_report",
        completed_stage="S5_ortho",
        global_percent=85.7,
        stage_progress=0.0,
        stage_timings={"S4_dsm": 0.3, "S5_ortho": 1677.5},
    )
    apply_event(_db(job), {"job_id": str(job.id), "event": "running", "stage_id": "S5_ortho"})
    assert job.status == "running"
    assert job.current_stage == "S5_ortho"
    assert job.completed_stage == "S4_dsm"
    assert job.global_percent == stage_rank("S5_ortho") / 7 * 100.0
    assert job.stage_timings["S5_ortho"] == 1677.5
    assert job.stage_timings["S4_dsm"] == 0.3


def test_stale_reentry_does_not_keep_the_dead_gap(tmp_path):
    """进程死了几个小时再进同一阶段，空档不能留在原来的开跑时间上。"""
    from datetime import datetime, timedelta, timezone

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    start = datetime.now(timezone.utc) - timedelta(hours=9)
    last = start + timedelta(minutes=16)
    (log_dir / "run.log").write_text(
        f"{start.strftime('%Y-%m-%d %H:%M:%S')} INFO job abc start S2_at 空三 / 稀疏重建\n"
        f"{last.strftime('%Y-%m-%d %H:%M:%S')} 交会  物方点 1，观测 2\n",
        encoding="utf-8",
    )
    job = _job(
        status="running",
        current_stage="S2_at",
        stage_timings={"_open": {"stage": "S2_at", "since": start.isoformat()}},
        log_dir=str(log_dir),
        output_dir=None,
    )
    apply_event(_db(job), {"job_id": str(job.id), "event": "running", "stage_id": "S2_at"})
    assert job.stage_timings["S2_at"] < 20 * 60
    assert job.stage_timings["S2_at"] > 15 * 60
    assert "_open" not in job.stage_timings


def test_stopped_gap_is_not_part_of_stage_clock():
    job = _job(status="running", current_stage="S5_ortho", completed_stage="S4_dsm", stage_timings={})
    apply_event(_db(job), {"job_id": str(job.id), "event": "running", "stage_id": "S5_ortho"})
    opened = job.stage_timings["_open"]["since"]
    apply_event(_db(job), {"job_id": str(job.id), "event": "failed", "error": "exit -9"})
    assert "_open" not in job.stage_timings
    assert job.stage_timings["S5_ortho"] >= 0
    apply_event(_db(job), {"job_id": str(job.id), "event": "running", "stage_id": "S5_ortho"})
    assert job.stage_timings["_open"]["since"] != opened
    assert job.stage_timings["_open"]["stage"] == "S5_ortho"


def test_stage_done_adds_to_saved_elapsed():
    job = _job(completed_stage="S4_dsm", stage_timings={"S5_ortho": 1677.5})
    apply_event(_db(job), {"job_id": str(job.id), "event": "stage_done", "stage_id": "S5_ortho", "elapsed_s": 1216.6})
    assert job.stage_timings["S5_ortho"] == 1677.5 + 1216.6


def test_running_event_still_advances():
    job = _job(current_stage="S4_dsm", completed_stage="S4_dsm", global_percent=71.0)
    apply_event(_db(job), {"job_id": str(job.id), "event": "running", "stage_id": "S5_ortho"})
    assert job.current_stage == "S5_ortho"
    assert job.completed_stage == "S4_dsm"
    assert job.global_percent == 71.0


def test_stage_done_advances_current_if_behind():
    job = _job(current_stage="S2_at", completed_stage="S2_at")
    apply_event(_db(job), {"job_id": str(job.id), "event": "stage_done", "stage_id": "S4_dsm"})
    assert job.completed_stage == "S4_dsm"
    assert job.current_stage == "S4_dsm"
