"""安装配置：按飞行日期选配置、手动指定优先、内置兜底、抄进任务。"""

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import pushbroom_calibration as cal
from app.services import pushbroom_mounts as mounts


def _profile(name, day, along=1.78, bore=-49.8):
    return SimpleNamespace(id=uuid.uuid4(), name=name, effective_from=day, along_offset_m=along, boresight_px=bore)


P0414 = _profile("0414 安装", date(2026, 4, 14))
P0419 = _profile("0419 安装", date(2026, 4, 19), along=1.3, bore=-30.0)
PROFILES = [P0414, P0419]


def test_flight_date_is_beijing_date():
    # 2026-04-13 17:00 UTC 是北京时间 4 月 14 日 01:00
    assert mounts.flight_date("2026-04-13T17:00:00+00:00") == date(2026, 4, 14)
    assert mounts.flight_date("2026-04-13T15:00:00+00:00") == date(2026, 4, 13)
    assert mounts.flight_date(None) is None
    assert mounts.flight_date("不是时间") is None


def test_latest_profile_not_after_flight_date_wins():
    assert mounts.resolve(PROFILES, None, date(2026, 4, 18)) == mounts.Resolved("date", P0414)
    assert mounts.resolve(PROFILES, None, date(2026, 4, 29)) == mounts.Resolved("date", P0419)


def test_effective_day_itself_counts():
    assert mounts.resolve(PROFILES, None, date(2026, 4, 19)).profile is P0419


def test_flight_before_every_profile_uses_builtin():
    assert mounts.resolve(PROFILES, None, date(2026, 4, 10)) == mounts.Resolved("builtin")
    assert mounts.resolve(PROFILES, None, None) == mounts.Resolved("builtin")
    assert mounts.resolve([], None, date(2026, 4, 29)) == mounts.Resolved("builtin")


def test_manual_override_beats_date():
    assert mounts.resolve(PROFILES, P0414.id, date(2026, 4, 29)) == mounts.Resolved("manual", P0414)
    # 指向已删除的配置时按日期走
    assert mounts.resolve(PROFILES, uuid.uuid4(), date(2026, 4, 29)).profile is P0419


def test_mount_out_shapes():
    assert mounts.mount_out(mounts.Resolved("builtin")) == {
        "id": None,
        "name": None,
        "source": "builtin",
        "along_offset_m": 1.7,
        "boresight_px": -19.0,
    }
    out = mounts.mount_out(mounts.Resolved("date", P0414))
    assert out == {"id": str(P0414.id), "name": "0414 安装", "source": "date", "along_offset_m": 1.78, "boresight_px": -49.8}


def test_apply_mount_copies_values_and_name():
    params = mounts.apply_mount({"bands": [1, 2]}, mounts.Resolved("manual", P0414))
    assert params == {
        "bands": [1, 2],
        "along_offset_m": 1.78,
        "boresight_px": -49.8,
        "mount_profile": {"id": str(P0414.id), "name": "0414 安装", "source": "manual"},
    }


def test_apply_mount_builtin_leaves_values_to_worker():
    params = mounts.apply_mount({"gsd_m": 0.04}, mounts.Resolved("builtin"))
    assert params == {"gsd_m": 0.04, "mount_profile": {"source": "builtin"}}


@pytest.mark.parametrize("key", ["along_offset_m", "boresight_px", "mount_profile"])
def test_job_cannot_carry_mount_fields(key):
    with pytest.raises(HTTPException) as exc:
        mounts.apply_mount({key: 1}, mounts.Resolved("builtin"))
    assert exc.value.status_code == 400
    assert "不能在任务里填写" in exc.value.detail


def test_count_matches_per_profile():
    flights = [
        ("A", "2026-04-14T05:35:44+00:00"),
        ("B", "2026-04-19T05:10:43+00:00"),
        ("C", "2026-04-29T05:34:09+00:00"),
        ("D", "2026-04-10T06:24:36+00:00"),
    ]
    counts = mounts.count_matches(PROFILES, {"C": P0414.id}, flights)
    assert counts == {str(P0414.id): 2, str(P0419.id): 1}


SETTINGS = Settings(jwt_secret="test-secret", jwt_algorithm="HS256")
USER = SimpleNamespace(id=7, username="admin")
RESULT = {
    "flights": [{"flight": "FigSpec_260419_132008", "calibration": {"usable": True}}],
    "combined": {"usable": True, "along_offset_m": 1.314, "boresight_px": -30.26, "along_se_m": 0.2, "boresight_se_px": 3.1},
}


def _done_run():
    return SimpleNamespace(id=uuid.uuid4(), status="done", result=RESULT, finished_at=datetime.now(timezone.utc))


def _token(run):
    return cal.issue_run_token(SETTINGS, str(run.id), datetime.now(timezone.utc) + timedelta(minutes=5))


def test_create_profile_takes_values_from_the_run(monkeypatch):
    run = _done_run()
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [P0414])
    db = MagicMock()
    db.get.return_value = run
    row = mounts.create_profile(
        db, SETTINGS, USER, run_id=run.id, run_token=_token(run), name="  0419 安装 ", effective_from=date(2026, 4, 19)
    )
    assert row.name == "0419 安装" and row.effective_from == date(2026, 4, 19)
    assert (row.along_offset_m, row.boresight_px, row.along_se_m, row.boresight_se_px) == (1.31, -30.3, 0.2, 3.1)
    assert row.flights == ["FigSpec_260419_132008"] and row.created_by == 7
    db.add.assert_called()


def test_create_profile_rejects_token_of_another_run(monkeypatch):
    run = _done_run()
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [])
    db = MagicMock()
    db.get.return_value = run
    with pytest.raises(HTTPException) as exc:
        mounts.create_profile(db, SETTINGS, USER, run_id=run.id, run_token=_token(_done_run()), name="x", effective_from=date(2026, 5, 1))
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("name", "day", "word"),
    [("0414 安装", date(2026, 5, 1), "名称"), ("新的", date(2026, 4, 14), "生效日期"), ("  ", date(2026, 5, 1), "名称")],
)
def test_create_profile_rejects_duplicate_or_blank(monkeypatch, name, day, word):
    run = _done_run()
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [P0414])
    db = MagicMock()
    db.get.return_value = run
    with pytest.raises(HTTPException) as exc:
        mounts.create_profile(db, SETTINGS, USER, run_id=run.id, run_token=_token(run), name=name, effective_from=day)
    assert word in exc.value.detail


def test_update_profile_changes_only_name_and_date(monkeypatch):
    row = _profile("旧名", date(2026, 4, 14))
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [row, P0419])
    db = MagicMock()
    db.get.return_value = row
    mounts.update_profile(db, USER, row.id, name="0414 安装", effective_from=date(2026, 4, 13))
    assert row.name == "0414 安装" and row.effective_from == date(2026, 4, 13)
    assert row.along_offset_m == 1.78
    with pytest.raises(HTTPException):
        mounts.update_profile(db, USER, row.id, effective_from=date(2026, 4, 19))


def test_delete_profile_also_drops_overrides():
    row = _profile("0414 安装", date(2026, 4, 14))
    db = MagicMock()
    db.get.return_value = row
    mounts.delete_profile(db, USER, row.id)
    db.execute.assert_called_once()
    db.delete.assert_called_once_with(row)


def test_set_override_creates_updates_and_clears():
    db = MagicMock()
    db.get.side_effect = lambda model, key: P0414 if model is mounts.PushbroomMountProfile else None
    mounts.set_override(db, USER, "FigSpec_A", P0414.id)
    added = db.add.call_args_list[0].args[0]
    assert added.flight_name == "FigSpec_A" and added.profile_id == P0414.id
    existing = SimpleNamespace(flight_name="FigSpec_A", profile_id=P0419.id)
    db2 = MagicMock()
    db2.get.side_effect = lambda model, key: P0414 if model is mounts.PushbroomMountProfile else existing
    mounts.set_override(db2, USER, "FigSpec_A", P0414.id)
    assert existing.profile_id == P0414.id
    db3 = MagicMock()
    db3.get.return_value = existing
    mounts.set_override(db3, USER, "FigSpec_A", None)
    db3.delete.assert_called_once_with(existing)


def test_legacy_effective_date_uses_earliest_flight_or_save_date():
    legacy = SimpleNamespace(flights=["A", "B"], created_at=datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc))
    assert mounts.legacy_effective_date(legacy, {"A": date(2026, 4, 19), "B": date(2026, 4, 14)}) == date(2026, 4, 14)
    assert mounts.legacy_effective_date(legacy, {}) == date(2026, 11, 20)


def test_migrate_legacy_runs_once(monkeypatch):
    legacy = SimpleNamespace(
        along_offset_m=1.78,
        along_se_m=0.11,
        boresight_px=-49.8,
        boresight_se_px=2.9,
        flights=["FigSpec_260414_134505"],
        created_by=1,
        created_at=datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(mounts, "_flight_dates", lambda root, names: {"FigSpec_260414_134505": date(2026, 4, 14)})
    db = MagicMock()
    db.scalar.side_effect = [None, legacy]
    row = mounts.migrate_legacy(db, "/data/input/pushbroom")
    assert row.name == "0414 安装" and row.effective_from == date(2026, 4, 14)
    assert row.along_offset_m == 1.78 and row.flights == ["FigSpec_260414_134505"]
    db.commit.assert_called_once()
    again = MagicMock()
    again.scalar.return_value = uuid.uuid4()
    assert mounts.migrate_legacy(again, "/data/input/pushbroom") is None
    again.add.assert_not_called()