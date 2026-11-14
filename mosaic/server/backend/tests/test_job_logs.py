from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.jobs import _fmt_log_ts, format_job_logs


def test_fmt_log_ts_utc():
    dt = datetime(2026, 9, 19, 10, 50, 1, tzinfo=timezone.utc)
    assert _fmt_log_ts(dt) == "2026-09-19 10:50:01"


def test_format_job_logs_from_db(tmp_path):
    job_id = uuid4()
    row = SimpleNamespace(
        created_at=datetime(2026, 9, 19, 10, 50, 1, tzinfo=timezone.utc),
        level="info",
        message="任务已创建",
    )
    db = MagicMock()
    db.scalars.return_value = [row]
    job = SimpleNamespace(id=job_id, log_dir=str(tmp_path / "missing"), output_dir=str(tmp_path))
    text = format_job_logs(db, job, tail=50)
    assert "任务已创建" in text
    assert "[info]" in text
