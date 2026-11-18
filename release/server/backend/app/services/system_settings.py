from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, SystemSetting

KEY_CLOCK_TOLERANCE_HOURS = "license_clock_tolerance_hours"
KEY_LEASE_HOURS = "license_lease_hours"
KEY_LEASE_INTERVAL_HOURS = "license_lease_interval_hours"

DEFAULT_LEASE_HOURS = 72.0
DEFAULT_LEASE_INTERVAL_HOURS = 6.0

SETTING_SEEDS: list[dict[str, str]] = [
    {
        "key": KEY_CLOCK_TOLERANCE_HOURS,
        "value": "1",
        "label": "回拨容差（小时）",
        "description": "客户 worker 时钟回拨检测的允许回拨时长。超过该值将拒绝运行并提示「系统时间异常，疑似时钟回拨」。",
        "used_by": "客户 worker：ms_mosaic.license_check.check_clock_rollback（环境变量 LICENSE_CLOCK_TOLERANCE_HOURS）；打交付包时写入 docker-compose.yml → worker.environment",
    },
    {
        "key": KEY_LEASE_HOURS,
        "value": "72",
        "label": "租约有效期（小时）",
        "description": "绑定许可每次领取的租约可离线使用的时长。断网超过该时长后须恢复网络才能继续使用，且不会超过许可本身的到期时间。保存后，客户下一次领取即按新时长签发。领取间隔必须小于本值。",
        "used_by": "发版台：签发租约时写入 expires_at（app.routers.licenses.check_license）。不写入客户交付包。",
    },
    {
        "key": KEY_LEASE_INTERVAL_HOURS,
        "value": "6",
        "label": "租约领取间隔（小时）",
        "description": "客户 worker 运行期间自动向发版台领取租约的间隔。每次启动也会领取一次。应小于租约有效期。",
        "used_by": "客户 worker：ms_mosaic.license_check.lease_check_interval_seconds（环境变量 LICENSE_LEASE_CHECK_INTERVAL_HOURS）；打交付包时写入 docker-compose.yml → worker.environment",
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


def _hours_from_text(raw: str, default: float, *, allow_zero: bool) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        hours = float(text)
    except ValueError:
        return None
    if hours < 0 or (hours == 0 and not allow_zero):
        return None
    return hours


def get_clock_tolerance_hours(db: Session, *, customer: Customer | None = None) -> float:
    if customer is not None:
        parsed = _hours_from_text(customer.clock_tolerance_hours, 1.0, allow_zero=True)
        if parsed is not None:
            return parsed
    raw = get_setting_value(db, KEY_CLOCK_TOLERANCE_HOURS, "1").strip() or "1"
    parsed = _hours_from_text(raw, 1.0, allow_zero=True)
    return 1.0 if parsed is None else parsed


def _positive_hours(db: Session, key: str, default: float) -> float:
    raw = get_setting_value(db, key, str(int(default))).strip() or str(int(default))
    parsed = _hours_from_text(raw, default, allow_zero=False)
    return default if parsed is None else parsed


def get_lease_hours(db: Session, *, customer: Customer | None = None) -> float:
    if customer is not None:
        parsed = _hours_from_text(customer.lease_hours, DEFAULT_LEASE_HOURS, allow_zero=False)
        if parsed is not None:
            return parsed
    return _positive_hours(db, KEY_LEASE_HOURS, DEFAULT_LEASE_HOURS)


def get_lease_interval_hours(db: Session, *, customer: Customer | None = None) -> float:
    if customer is not None:
        parsed = _hours_from_text(customer.lease_interval_hours, DEFAULT_LEASE_INTERVAL_HOURS, allow_zero=False)
        if parsed is not None:
            return parsed
    return _positive_hours(db, KEY_LEASE_INTERVAL_HOURS, DEFAULT_LEASE_INTERVAL_HOURS)


def format_hours(hours: float) -> str:
    if hours == int(hours):
        return str(int(hours))
    return str(hours)


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
