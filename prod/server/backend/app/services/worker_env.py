from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SystemSetting

WORKER_SETTING_KEY = "worker_env"


def load_worker_env(db: Session | None) -> dict[str, Any]:
    if db is None:
        return {}
    row = db.get(SystemSetting, WORKER_SETTING_KEY)
    if row is None or not isinstance(row.value, dict):
        return {}
    return dict(row.value)


def _as_container_data_path(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    if text == "/data" or text.startswith("/data/") or text.startswith("/data,"):
        return text
    return fallback


def save_worker_env(db: Session, payload: dict[str, str]) -> dict[str, str]:
    cleaned = {
        "data_roots": _as_container_data_path(payload.get("data_roots") or "", "/data"),
        "default_input_dir": _as_container_data_path(
            payload.get("default_input_dir") or "", "/data/input/MAX_20251017/MAX_20251017_001"
        ),
        "default_output_dir": _as_container_data_path(
            payload.get("default_output_dir") or "", "/data/output/runs"
        ),
    }
    row = db.get(SystemSetting, WORKER_SETTING_KEY)
    if row is None:
        row = SystemSetting(key=WORKER_SETTING_KEY, value=cleaned)
        db.add(row)
    else:
        row.value = cleaned
    db.commit()
    db.refresh(row)
    return cleaned


def apply_worker_env(db: Session | None, settings: Settings) -> Settings:
    ov = load_worker_env(db)
    updates: dict[str, str] = {}
    if (ov.get("data_roots") or "").strip():
        updates["data_roots"] = ov["data_roots"].strip()
    if "default_input_dir" in ov:
        updates["default_input_dir"] = (ov.get("default_input_dir") or "").strip()
    if "default_output_dir" in ov:
        updates["default_output_dir"] = (ov.get("default_output_dir") or "").strip()
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def worker_env_view(db: Session | None, settings: Settings) -> dict[str, Any]:
    """compose Worker 容器环境（只读）+ 可改的数据路径。"""
    effective = apply_worker_env(db, settings)
    ov = load_worker_env(db)
    return {
        "rabbitmq_url": settings.rabbitmq_url,
        "pythonpath": "/app",
        "workdir": "/app",
        "mplbackend": "Agg",
        "data_roots": effective.data_roots,
        "default_input_dir": effective.default_input_dir,
        "default_output_dir": effective.default_output_dir,
        "source": "db" if ov else "env",
    }
