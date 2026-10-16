import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.jobs import estimate_eta_seconds
from app.services.rbac import _ensure_public


def test_cli_stays_on_every_saved_menu():
    assert "cli" in _ensure_public(["profiles", "jobs"])
    assert _ensure_public(["cli", "inspect"]).count("cli") == 1


def test_eta_uses_median_of_finished_jobs():
    now = datetime.now(timezone.utc)
    past = SimpleNamespace(
        id=uuid.uuid4(),
        started_at=now - timedelta(hours=2),
        finished_at=now,
    )
    job = SimpleNamespace(id=uuid.uuid4(), started_at=now)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [past]
    eta = estimate_eta_seconds(db, job, 50)
    assert eta == 3600


def test_eta_extrapolates_when_there_is_no_history():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(id=uuid.uuid4(), started_at=now - timedelta(minutes=10))
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    eta = estimate_eta_seconds(db, job, 25)
    assert eta == 1800


def test_eta_is_zero_when_finished():
    job = SimpleNamespace(id=uuid.uuid4(), started_at=None)
    assert estimate_eta_seconds(MagicMock(), job, 100) == 0
