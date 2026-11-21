from datetime import datetime, timezone

from app.services.jobs import default_job_title


def test_default_job_title_uses_beijing_clock():
    when = datetime(2026, 11, 14, 3, 8, 45, tzinfo=timezone.utc)
    assert default_job_title(1, when) == "#1 2026-11-14 11:08"
