"""推扫安装配置：按飞行日期选配置、手动指定、抄进任务。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomCalibration, PushbroomCalibrationRun, PushbroomFlightProfile, PushbroomMountProfile, User
from app.services.audit import record_audit
from app.services.pushbroom_calibration import assert_run_token, usable_combined

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def profile_out(row: Any, matched: int, username: str | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "effective_from": row.effective_from.isoformat(),
        "along_offset_m": row.along_offset_m,
        "along_se_m": row.along_se_m,
        "boresight_px": row.boresight_px,
        "boresight_se_px": row.boresight_se_px,
        "flights": list(row.flights or []),
        "created_by": username,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "matched": matched,
    }


def _clean_name(name: str | None) -> str:
    text = (name or "").strip()
    if not text:
        raise HTTPException(400, "请填写名称")
    if len(text) > 64:
        raise HTTPException(400, "名称最多 64 个字")
    return text


def _assert_unique(db: Session, *, name: str | None, day: date | None, skip: uuid.UUID | None = None) -> None:
    for row in load_profiles(db):
        if row.id == skip:
            continue
        if name is not None and row.name == name:
            raise HTTPException(400, f"名称与「{row.name}」重复")
        if day is not None and row.effective_from == day:
            raise HTTPException(400, f"生效日期与「{row.name}」重复")


def _get_profile(db: Session, profile_id: uuid.UUID) -> PushbroomMountProfile:
    row = db.get(PushbroomMountProfile, profile_id)
    if row is None:
        raise HTTPException(404, "安装配置不存在")
    return row


def create_profile(
    db: Session,
    settings: Settings,
    user: User,
    *,
    run_id: uuid.UUID,
    run_token: str,
    name: str,
    effective_from: date,
) -> PushbroomMountProfile:
    """用一次标定的结果建配置。数值只从标定记录取。"""
    run = db.get(PushbroomCalibrationRun, run_id)
    if run is None or run.status != "done":
        raise HTTPException(400, "标定结果已失效，请重新标定")
    assert_run_token(settings, run_token, str(run.id))
    values, flights = usable_combined(run.result)
    clean = _clean_name(name)
    _assert_unique(db, name=clean, day=effective_from)
    now = _utcnow()
    row = PushbroomMountProfile(
        id=uuid.uuid4(),
        name=clean,
        effective_from=effective_from,
        flights=flights,
        created_by=user.id,
        created_at=now,
        updated_at=now,
        **values,
    )
    db.add(row)
    record_audit(
        db,
        user.id,
        "pushbroom.mount.create",
        {"name": clean, "effective_from": effective_from.isoformat(), "run_id": str(run.id), **values},
    )
    return row


def update_profile(
    db: Session,
    user: User,
    profile_id: uuid.UUID,
    *,
    name: str | None = None,
    effective_from: date | None = None,
) -> PushbroomMountProfile:
    """只改名称和生效日期，数值不能改。"""
    row = _get_profile(db, profile_id)
    clean = _clean_name(name) if name is not None else None
    _assert_unique(db, name=clean, day=effective_from, skip=row.id)
    before = {"name": row.name, "effective_from": row.effective_from.isoformat()}
    if clean is not None:
        row.name = clean
    if effective_from is not None:
        row.effective_from = effective_from
    row.updated_at = _utcnow()
    after = {"name": row.name, "effective_from": row.effective_from.isoformat()}
    record_audit(db, user.id, "pushbroom.mount.update", {"id": str(row.id), "before": before, "after": after})
    return row


def delete_profile(db: Session, user: User, profile_id: uuid.UUID) -> None:
    """删除配置和指向它的手动指定。已提交的任务各自存着数值，不受影响。"""
    row = _get_profile(db, profile_id)
    detail = {"id": str(row.id), "name": row.name, "effective_from": row.effective_from.isoformat()}
    db.execute(delete(PushbroomFlightProfile).where(PushbroomFlightProfile.profile_id == row.id))
    db.delete(row)
    record_audit(db, user.id, "pushbroom.mount.delete", detail)


def set_override(db: Session, user: User, flight_name: str, profile_id: uuid.UUID | None) -> None:
    """给单个架次手动指定配置；profile_id 为空表示撤销，回到按日期。"""
    name = (flight_name or "").strip()
    if not name or "/" in name or name in {".", ".."}:
        raise HTTPException(400, "架次名无效")
    existing = db.get(PushbroomFlightProfile, name)
    if profile_id is None:
        if existing is not None:
            db.delete(existing)
        record_audit(db, user.id, "pushbroom.mount.override", {"flight": name, "profile_id": None})
        return
    _get_profile(db, profile_id)
    if existing is None:
        db.add(PushbroomFlightProfile(flight_name=name, profile_id=profile_id, created_by=user.id, created_at=_utcnow()))
    else:
        existing.profile_id = profile_id
    record_audit(db, user.id, "pushbroom.mount.override", {"flight": name, "profile_id": str(profile_id)})


def legacy_effective_date(row: Any, dates: dict[str, date]) -> date:
    found = [dates[name] for name in row.flights or [] if name in dates]
    if found:
        return min(found)
    return (row.created_at or _utcnow()).astimezone(CST).date()


def _flight_dates(input_root: str, names: list[str]) -> dict[str, date]:
    from app.services.pushbroom_check import find_flights, quick_check

    wanted = set(names)
    out: dict[str, date] = {}
    for path in find_flights(input_root):
        if path.name not in wanted:
            continue
        day = flight_date(quick_check(path).flight.get("started_at"))
        if day is not None:
            out[path.name] = day
    return out


def migrate_legacy(db: Session, input_root: str) -> PushbroomMountProfile | None:
    """配置表为空、旧标定表有记录时，把最新一条转成一组配置。只做一次。"""
    if db.scalar(select(PushbroomMountProfile.id).limit(1)) is not None:
        return None
    legacy = db.scalar(select(PushbroomCalibration).order_by(PushbroomCalibration.created_at.desc()).limit(1))
    if legacy is None:
        return None
    day = legacy_effective_date(legacy, _flight_dates(input_root, list(legacy.flights or [])))
    row = PushbroomMountProfile(
        id=uuid.uuid4(),
        name=f"{day:%m%d} 安装",
        effective_from=day,
        along_offset_m=legacy.along_offset_m,
        along_se_m=legacy.along_se_m,
        boresight_px=legacy.boresight_px,
        boresight_se_px=legacy.boresight_se_px,
        flights=list(legacy.flights or []),
        created_by=legacy.created_by,
        created_at=legacy.created_at or _utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    return row