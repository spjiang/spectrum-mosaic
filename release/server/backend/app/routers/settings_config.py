from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.db import get_db
from app.models import Admin
from app.schemas import SystemSettingOut, SystemSettingUpdateIn, SystemSettingsBatchIn
from app.services.audit import write_audit
from app.services.system_settings import (
    KEY_CLOCK_TOLERANCE_HOURS,
    KEY_LEASE_HOURS,
    KEY_LEASE_INTERVAL_HOURS,
    get_lease_hours,
    get_lease_interval_hours,
    list_settings,
    update_setting,
)

router = APIRouter(prefix="/api/settings/config", tags=["settings"])


def _format_hours(hours: float) -> str:
    if hours == int(hours):
        return str(int(hours))
    return str(hours)


def _validate_positive_hours(text: str, label: str) -> str:
    try:
        hours = float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{label}须为数字（小时）") from exc
    if hours <= 0:
        raise HTTPException(status_code=400, detail=f"{label}必须大于 0")
    if hours > 24 * 365:
        raise HTTPException(status_code=400, detail=f"{label}过大（上限 8760 小时）")
    return _format_hours(hours)


def _validate_value(key: str, value: str) -> str:
    text = (value or "").strip()
    if key == KEY_CLOCK_TOLERANCE_HOURS:
        try:
            hours = float(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="回拨容差须为数字（小时）") from exc
        if hours < 0:
            raise HTTPException(status_code=400, detail="回拨容差不能为负数")
        if hours > 24 * 30:
            raise HTTPException(status_code=400, detail="回拨容差过大（上限 720 小时）")
        return _format_hours(hours)
    if key == KEY_LEASE_HOURS:
        return _validate_positive_hours(text, "租约有效期")
    if key == KEY_LEASE_INTERVAL_HOURS:
        return _validate_positive_hours(text, "租约领取间隔")
    if not text:
        raise HTTPException(status_code=400, detail="配置值不能为空")
    return text


def _assert_lease_window(db: Session, pending: dict[str, str]) -> None:
    if KEY_LEASE_HOURS in pending:
        lease_hours = float(pending[KEY_LEASE_HOURS])
    else:
        lease_hours = get_lease_hours(db)
    if KEY_LEASE_INTERVAL_HOURS in pending:
        interval_hours = float(pending[KEY_LEASE_INTERVAL_HOURS])
    else:
        interval_hours = get_lease_interval_hours(db)
    if interval_hours >= lease_hours:
        raise HTTPException(status_code=400, detail="租约领取间隔必须小于租约有效期")


@router.get("", response_model=list[SystemSettingOut])
def get_config(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> list:
    return list_settings(db)


@router.put("/{key}", response_model=SystemSettingOut)
def put_config_item(
    key: str,
    body: SystemSettingUpdateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    value = _validate_value(key, body.value)
    _assert_lease_window(db, {key: value})
    row = update_setting(db, key, value)
    if row is None:
        raise HTTPException(status_code=404, detail="配置项不存在")
    write_audit(
        db,
        actor=admin.username,
        action="settings.config.update",
        object_type="system_setting",
        object_id=key,
        detail=f"value={value}",
    )
    return row


@router.put("", response_model=list[SystemSettingOut])
def put_config_batch(
    body: SystemSettingsBatchIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> list:
    pending: dict[str, str] = {}
    for item in body.items:
        key = (item.get("key") or "").strip()
        if not key:
            continue
        pending[key] = _validate_value(key, item.get("value") or "")
    _assert_lease_window(db, pending)
    updated = []
    for key, value in pending.items():
        row = update_setting(db, key, value)
        if row is None:
            raise HTTPException(status_code=404, detail=f"配置项不存在: {key}")
        write_audit(
            db,
            actor=admin.username,
            action="settings.config.update",
            object_type="system_setting",
            object_id=key,
            detail=f"value={value}",
        )
        updated.append(row)
    return updated
