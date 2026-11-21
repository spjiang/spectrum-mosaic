"""安装配置：按飞行日期选配置、手动指定优先、内置兜底、抄进任务。"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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