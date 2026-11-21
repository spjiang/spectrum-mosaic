"""推扫安装配置：按飞行日期选配置、手动指定、抄进任务。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PushbroomFlightProfile, PushbroomMountProfile

CST = timezone(timedelta(hours=8))
BUILTIN = {"along_offset_m": 1.7, "boresight_px": -19.0}
_JOB_FORBIDDEN = ("along_offset_m", "boresight_px", "mount_profile")


@dataclass(frozen=True)
class Resolved:
    # manual：手动指定；date：按生效日期；builtin：没有对应配置
    source: str
    profile: Any | None = None


def flight_date(started_at: str | None) -> date | None:
    """飞行开始时间对应的北京时间日期。"""
    if not started_at:
        return None
    try:
        moment = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(CST).date()


def resolve(profiles: Iterable[Any], override_id: uuid.UUID | None, day: date | None) -> Resolved:
    rows = list(profiles)
    if override_id is not None:
        for row in rows:
            if row.id == override_id:
                return Resolved("manual", row)
    if day is not None:
        eligible = [row for row in rows if row.effective_from <= day]
        if eligible:
            return Resolved("date", max(eligible, key=lambda row: row.effective_from))
    return Resolved("builtin")


def mount_out(res: Resolved) -> dict[str, Any]:
    if res.profile is None:
        return {"id": None, "name": None, "source": "builtin", **BUILTIN}
    row = res.profile
    return {
        "id": str(row.id),
        "name": row.name,
        "source": res.source,
        "along_offset_m": row.along_offset_m,
        "boresight_px": row.boresight_px,
    }


def apply_mount(params: dict[str, Any], res: Resolved) -> dict[str, Any]:
    """把选中的配置抄进任务参数。内置时不写数值，由 worker 用自己的内置值。"""
    if any(key in params for key in _JOB_FORBIDDEN):
        raise HTTPException(400, "安装参数由安装配置决定，不能在任务里填写")
    out = dict(params)
    if res.profile is None:
        out["mount_profile"] = {"source": "builtin"}
        return out
    row = res.profile
    out["along_offset_m"] = float(row.along_offset_m)
    out["boresight_px"] = float(row.boresight_px)
    out["mount_profile"] = {"id": str(row.id), "name": row.name, "source": res.source}
    return out


def count_matches(
    profiles: list[Any], overrides: dict[str, uuid.UUID], flights: list[tuple[str, str | None]]
) -> dict[str, int]:
    counts = {str(row.id): 0 for row in profiles}
    for name, started_at in flights:
        res = resolve(profiles, overrides.get(name), flight_date(started_at))
        if res.profile is not None:
            counts[str(res.profile.id)] += 1
    return counts


def load_profiles(db: Session) -> list[PushbroomMountProfile]:
    return list(db.scalars(select(PushbroomMountProfile).order_by(PushbroomMountProfile.effective_from.asc())))


def load_overrides(db: Session) -> dict[str, uuid.UUID]:
    return {row.flight_name: row.profile_id for row in db.scalars(select(PushbroomFlightProfile))}


def resolve_for(db: Session, flight_name: str, started_at: str | None) -> Resolved:
    return resolve(load_profiles(db), load_overrides(db).get(flight_name), flight_date(started_at))