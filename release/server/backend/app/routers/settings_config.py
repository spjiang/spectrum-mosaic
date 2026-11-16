from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.db import get_db
from app.models import Admin
from app.schemas import SystemSettingOut, SystemSettingUpdateIn, SystemSettingsBatchIn
from app.services.audit import write_audit
from app.services.system_settings import KEY_CLOCK_TOLERANCE_HOURS, list_settings, update_setting

router = APIRouter(prefix="/api/settings/config", tags=["settings"])


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
        # normalize
        if hours == int(hours):
            return str(int(hours))
        return str(hours)
    if not text:
        raise HTTPException(status_code=400, detail="配置值不能为空")
    return text


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
    updated = []
    for item in body.items:
        key = (item.get("key") or "").strip()
        if not key:
            continue
        value = _validate_value(key, item.get("value") or "")
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
