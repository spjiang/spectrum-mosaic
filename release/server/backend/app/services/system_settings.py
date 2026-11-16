from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting

KEY_CLOCK_TOLERANCE_HOURS = "license_clock_tolerance_hours"

SETTING_SEEDS: list[dict[str, str]] = [
    {
        "key": KEY_CLOCK_TOLERANCE_HOURS,
        "value": "1",
        "label": "回拨容差（小时）",
        "description": "客户 worker 时钟回拨检测的允许回拨时长。超过该值将拒绝运行并提示「系统时间异常，疑似时钟回拨」。",
        "used_by": "客户 worker：ms_mosaic.license_check.check_clock_rollback（环境变量 LICENSE_CLOCK_TOLERANCE_HOURS）；打交付包时写入 docker-compose.yml → worker.environment",
    },
]


def ensure_system_settings(db: Session) -> None:
    for seed in SETTING_SEEDS:
        row = db.get(SystemSetting, seed["key"])
        if row is None:
            db.add(
                SystemSetting(
                    key=seed["key"],
                    value=seed["value"],
                    label=seed["label"],
                    description=seed["description"],
                    used_by=seed["used_by"],
                )
            )
        else:
            # Keep value; refresh metadata so UI copy stays current after upgrades.
            row.label = seed["label"]
            row.description = seed["description"]
            row.used_by = seed["used_by"]
    db.commit()


def get_setting_value(db: Session, key: str, default: str = "") -> str:
    row = db.get(SystemSetting, key)
    if row is None or row.value is None:
        return default
    return str(row.value)


def get_clock_tolerance_hours(db: Session) -> float:
    raw = get_setting_value(db, KEY_CLOCK_TOLERANCE_HOURS, "1").strip() or "1"
    try:
        hours = float(raw)
    except ValueError:
        hours = 1.0
    if hours < 0:
        hours = 0.0
    return hours


def list_settings(db: Session) -> list[SystemSetting]:
    return list(db.scalars(select(SystemSetting).order_by(SystemSetting.key)).all())


def update_setting(db: Session, key: str, value: str) -> SystemSetting | None:
    row = db.get(SystemSetting, key)
    if row is None:
        return None
    row.value = value
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
