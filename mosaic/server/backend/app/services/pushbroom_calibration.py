"""推扫相机安装标定。在后台线程里调计算服务，结果写进标定记录，页面轮询。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomCalibrationRun, User
from app.services.audit import record_audit
from app.services.mq import queue_stats

log = logging.getLogger(__name__)

TOKEN_TYPE = "pushbroom_calibration"
TOKEN_SECONDS = 30 * 60
QUEUE_CALIBRATE = "pushbroom.calibrate"
RUNS_KEPT = 10
STALE_ERROR = "服务重启，标定中断"
_DIGITS = {"along_offset_m": 2, "boresight_px": 1, "along_se_m": 2, "boresight_se_px": 1}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def issue_run_token(settings: Settings, run_id: str, expires: datetime) -> str:
    payload = {"typ": TOKEN_TYPE, "run": run_id, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def assert_run_token(settings: Settings, token: str, run_id: str) -> None:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "标定结果已失效，请重新标定") from exc
    if claims.get("typ") != TOKEN_TYPE or claims.get("run") != run_id:
        raise HTTPException(400, "标定结果已失效，请重新标定")


def usable_names(result: dict[str, Any]) -> list[str]:
    names = []
    for item in result.get("flights") or []:
        if not isinstance(item, dict):
            continue
        cal = item.get("calibration") or {}
        if cal.get("usable") and item.get("flight"):
            names.append(str(item["flight"]))
    return names


def _has_usable(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    return bool((result.get("combined") or {}).get("usable")) and bool(usable_names(result))


def usable_combined(result: dict[str, Any] | None) -> tuple[dict[str, float], list[str]]:
    """可保存的合并值（已取位）和参与的架次。不可用或超出范围时 400。"""
    if not _has_usable(result):
        raise HTTPException(400, "这次标定没有可用结果")
    combined = result["combined"]  # type: ignore[index]
    try:
        values = {key: round(float(combined[key]), digits) for key, digits in _DIGITS.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, "这次标定没有可用结果") from exc
    if not -20 <= values["along_offset_m"] <= 20:
        raise HTTPException(400, "沿航向偏移超出范围")
    if not -400 <= values["boresight_px"] <= 400:
        raise HTTPException(400, "狭缝中心偏置超出范围")
    if values["along_se_m"] < 0 or values["boresight_se_px"] < 0:
        raise HTTPException(400, "不确定度无效")
    return values, usable_names(result)  # type: ignore[arg-type]


def run_out(settings: Settings, row: Any, username: str | None = None) -> dict[str, Any]:
    dirs = list(row.input_dirs or [])
    out: dict[str, Any] = {
        "id": str(row.id),
        "status": row.status,
        "input_dirs": dirs,
        "flights": [Path(d).name for d in dirs],
        "result": row.result,
        "error": row.error,
        "created_by": username,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
        "expires_at": None,
    }
    if row.status == "done" and row.finished_at is not None and _has_usable(row.result):
        expires = row.finished_at + timedelta(seconds=TOKEN_SECONDS)
        if expires > _utcnow():
            out["run_token"] = issue_run_token(settings, str(row.id), expires)
            out["expires_at"] = expires.isoformat()
    return out


def _online(settings: Settings) -> bool:
    consumers = queue_stats(settings.rabbitmq_url, QUEUE_CALIBRATE).get("consumers")
    return bool(consumers)


def _rpc(settings: Settings, input_dirs: list[str]) -> dict[str, Any]:
    import pika

    timeout = min(600, len(input_dirs) * 120) + 15
    conn = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    box: dict[str, bytes] = {}
    try:
        ch = conn.channel()
        ch.queue_declare(queue=QUEUE_CALIBRATE, durable=True)
        reply = ch.queue_declare(queue="", exclusive=True)
        corr = str(uuid.uuid4())
        ch.basic_publish(
            exchange="",
            routing_key=QUEUE_CALIBRATE,
            body=json.dumps({"schema_version": 1, "input_dirs": input_dirs}, ensure_ascii=False).encode(),
            properties=pika.BasicProperties(reply_to=reply.method.queue, correlation_id=corr, content_type="application/json"),
        )

        def on_message(_ch, _method, props, payload: bytes) -> None:
            if props.correlation_id == corr:
                box["body"] = payload
                _ch.stop_consuming()

        ch.basic_consume(queue=reply.method.queue, on_message_callback=on_message, auto_ack=True)
        conn.call_later(timeout, lambda: ch.stop_consuming())
        ch.start_consuming()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if "body" not in box:
        raise RuntimeError("标定超时")
    try:
        data = json.loads(box["body"].decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("标定没有返回结果") from exc
    if not isinstance(data, dict):
        raise RuntimeError("标定没有返回结果")
    return data


def _launch(settings: Settings, run_id: uuid.UUID, input_dirs: list[str]) -> None:
    threading.Thread(
        target=execute_run, args=(settings, run_id, input_dirs), name="pushbroom-calibrate", daemon=True
    ).start()


def start_run(
    db: Session,
    settings: Settings,
    user: User,
    input_dirs: list[str],
    *,
    launch: Callable[[Settings, uuid.UUID, list[str]], None] | None = None,
) -> PushbroomCalibrationRun:
    """建一条进行中的标定记录，交给后台线程，立刻返回。"""
    busy = db.scalar(select(PushbroomCalibrationRun).where(PushbroomCalibrationRun.status == "running").limit(1))
    if busy is not None:
        raise HTTPException(409, "已有一次标定在进行，请等它结束")
    if launch is None and not _online(settings):
        raise HTTPException(409, "推扫计算服务未在线")
    run_id = uuid.uuid4()
    row = PushbroomCalibrationRun(
        id=run_id,
        status="running",
        input_dirs=list(input_dirs),
        created_by=user.id,
        created_at=_utcnow(),
    )
    db.add(row)
    record_audit(db, user.id, "pushbroom.calibration.start", {"run_id": str(run_id), "input_dirs": list(input_dirs)})
    (launch or _launch)(settings, run_id, list(input_dirs))
    return row


def execute_run(
    settings: Settings,
    run_id: uuid.UUID,
    input_dirs: list[str],
    *,
    rpc: Callable[[list[str]], dict[str, Any]] | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """后台线程：调计算服务，把结果或失败原因写回记录。"""
    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal
    call = rpc or (lambda dirs: _rpc(settings, dirs))
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        data = call(input_dirs)
        if not isinstance(data, dict) or "combined" not in data:
            detail = data.get("error") if isinstance(data, dict) else None
            raise RuntimeError(detail or "标定没有返回结果")
        result = data
    except Exception as exc:  # noqa: BLE001
        log.warning("pushbroom calibrate %s failed: %s", run_id, exc)
        error = str(exc) or "标定失败"
    db = session_factory()
    try:
        row = db.get(PushbroomCalibrationRun, run_id)
        if row is None or row.status != "running":
            return
        row.status = "failed" if error else "done"
        row.result = result
        row.error = error
        row.finished_at = _utcnow()
        db.commit()
    finally:
        db.close()


def list_runs(db: Session) -> list[PushbroomCalibrationRun]:
    q = select(PushbroomCalibrationRun).order_by(PushbroomCalibrationRun.created_at.desc()).limit(RUNS_KEPT)
    return list(db.scalars(q))


def get_run(db: Session, run_id: uuid.UUID) -> PushbroomCalibrationRun:
    row = db.get(PushbroomCalibrationRun, run_id)
    if row is None:
        raise HTTPException(404, "标定记录不存在")
    return row


def fail_stale_runs(db: Session) -> int:
    """启动时把上次没跑完的标定记成中断。后台线程随进程一起没了。"""
    rows = list(db.scalars(select(PushbroomCalibrationRun).where(PushbroomCalibrationRun.status == "running")))
    now = _utcnow()
    for row in rows:
        row.status = "failed"
        row.error = STALE_ERROR
        row.finished_at = now
    if rows:
        db.commit()
    return len(rows)