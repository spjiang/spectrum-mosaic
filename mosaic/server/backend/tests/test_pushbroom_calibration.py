"""后台标定：同时只一次、结果写回、重启收口、凭证只认这次标定且 30 分钟有效。"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import pushbroom_calibration as cal

SETTINGS = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
COMBINED = {"usable": True, "along_offset_m": 1.784, "boresight_px": -49.83, "along_se_m": 0.114, "boresight_se_px": 2.94}
USABLE = {
    "flights": [
        {"flight": "FigSpec_A", "calibration": {"usable": True}},
        {"flight": "FigSpec_B", "calibration": {"usable": False}},
    ],
    "combined": COMBINED,
}
USER = SimpleNamespace(id=7, username="admin")


def _row(**kw):
    base = dict(
        id=uuid.uuid4(),
        status="done",
        input_dirs=["/data/input/pushbroom/FigSpec_A", "/data/input/pushbroom/FigSpec_B"],
        result=USABLE,
        error=None,
        created_by=7,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        finished_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _RunDb:
    def __init__(self, row):
        self.row = row
        self.committed = False
        self.closed = False

    def get(self, _model, _id):
        return self.row

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_start_refuses_while_another_run_is_going():
    db = MagicMock()
    db.scalar.return_value = _row(status="running")
    with pytest.raises(HTTPException) as exc:
        cal.start_run(db, SETTINGS, USER, ["/d/A"], launch=lambda *_: None)
    assert exc.value.status_code == 409


def test_start_records_running_and_launches_once():
    db = MagicMock()
    db.scalar.return_value = None
    calls = []
    row = cal.start_run(db, SETTINGS, USER, ["/d/A", "/d/B"], launch=lambda *args: calls.append(args))
    assert row.status == "running" and row.input_dirs == ["/d/A", "/d/B"] and row.created_by == 7
    assert calls == [(SETTINGS, row.id, ["/d/A", "/d/B"])]
    db.add.assert_called()
    db.commit.assert_called()


def test_execute_writes_result_back():
    row = _row(status="running", result=None, finished_at=None)
    db = _RunDb(row)
    cal.execute_run(SETTINGS, row.id, row.input_dirs, rpc=lambda dirs: USABLE, session_factory=lambda: db)
    assert row.status == "done" and row.result == USABLE and row.finished_at is not None
    assert db.committed and db.closed


def test_execute_records_failure_reason():
    row = _row(status="running", result=None, finished_at=None)

    def boom(_dirs):
        raise RuntimeError("标定超时")

    cal.execute_run(SETTINGS, row.id, row.input_dirs, rpc=boom, session_factory=lambda: _RunDb(row))
    assert row.status == "failed" and row.error == "标定超时"
    row2 = _row(status="running", result=None, finished_at=None)
    cal.execute_run(SETTINGS, row2.id, [], rpc=lambda d: {"error": "进程退出"}, session_factory=lambda: _RunDb(row2))
    assert row2.status == "failed" and row2.error == "进程退出"


def test_execute_leaves_rows_that_are_no_longer_running():
    row = _row(status="failed", error=cal.STALE_ERROR)
    cal.execute_run(SETTINGS, row.id, [], rpc=lambda d: USABLE, session_factory=lambda: _RunDb(row))
    assert row.status == "failed" and row.error == cal.STALE_ERROR


def test_fail_stale_runs_marks_running_as_interrupted():
    stale = _row(status="running", finished_at=None)
    db = MagicMock()
    db.scalars.return_value = [stale]
    assert cal.fail_stale_runs(db) == 1
    assert stale.status == "failed" and stale.error == cal.STALE_ERROR and stale.finished_at is not None


def test_run_out_issues_token_only_for_fresh_usable_result():
    row = _row()
    out = cal.run_out(SETTINGS, row, "admin")
    assert out["flights"] == ["FigSpec_A", "FigSpec_B"]
    assert out["created_by"] == "admin"
    cal.assert_run_token(SETTINGS, out["run_token"], str(row.id))
    with pytest.raises(HTTPException):
        cal.assert_run_token(SETTINGS, out["run_token"], str(uuid.uuid4()))
    old = _row(finished_at=datetime.now(timezone.utc) - timedelta(minutes=31))
    assert "run_token" not in cal.run_out(SETTINGS, old)
    unusable = _row(result={"flights": [], "combined": {"usable": False, "note": "往返航带不够"}})
    assert "run_token" not in cal.run_out(SETTINGS, unusable)
    assert "run_token" not in cal.run_out(SETTINGS, _row(status="running", result=None, finished_at=None))


def test_expired_token_is_rejected():
    row_id = str(uuid.uuid4())
    token = cal.issue_run_token(SETTINGS, row_id, datetime.now(timezone.utc) - timedelta(seconds=5))
    with pytest.raises(HTTPException) as exc:
        cal.assert_run_token(SETTINGS, token, row_id)
    assert "重新标定" in exc.value.detail


def test_usable_combined_rounds_and_lists_usable_flights():
    values, names = cal.usable_combined(USABLE)
    assert values == {"along_offset_m": 1.78, "boresight_px": -49.8, "along_se_m": 0.11, "boresight_se_px": 2.9}
    assert names == ["FigSpec_A"]
    with pytest.raises(HTTPException):
        cal.usable_combined({"flights": [], "combined": {"usable": False}})
    with pytest.raises(HTTPException):
        cal.usable_combined({**USABLE, "combined": {**COMBINED, "boresight_px": 900}})