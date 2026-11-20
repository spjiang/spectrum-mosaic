"""推扫相机安装标定。建议值由计算服务算出，确认后写入之后新建的任务。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomCalibration, User
from app.services.audit import record_audit
from app.services.mq import queue_stats

TOKEN_TYPE = "pushbroom_calibration"
TOKEN_SECONDS = 30 * 60
QUEUE_CALIBRATE = "pushbroom.calibrate"
BUILTIN = {"along_offset_m": 1.7, "boresight_px": -19.0}
_DIGITS = {"along_offset_m": 2, "boresight_px": 1, "along_se_m": 2, "boresight_se_px": 1}


def latest_calibration(db: Session) -> PushbroomCalibration | None:
    return db.scalar(select(PushbroomCalibration).order_by(PushbroomCalibration.created_at.desc()).limit(1))


def with_active_calibration(params: dict[str, Any], row: Any | None) -> dict[str, Any]:
    """把生效标定抄进任务参数。调用方自己带了安装参数则拒绝。"""
    if "along_offset_m" in params or "boresight_px" in params:
        raise HTTPException(400, "安装参数由相机标定决定，不能在任务里填写")
    if row is None:
        return params
    out = dict(params)
    out["along_offset_m"] = float(row.along_offset_m)
    out["boresight_px"] = float(row.boresight_px)
    return out


def public_state(db: Session, row: PushbroomCalibration | None) -> dict[str, Any]:
    if row is None:
        return {"active": False, "builtin": dict(BUILTIN)}
    username = None
    if row.created_by is not None:
        user = db.get(User, row.created_by)
        username = user.username if user is not None else None
    created = row.created_at
    return {
        "active": True,
        "along_offset_m": row.along_offset_m,
        "boresight_px": row.boresight_px,
        "along_se_m": row.along_se_m,
        "boresight_se_px": row.boresight_se_px,
        "flights": list(row.flights or []),
        "created_at": created.isoformat() if created is not None else None,
        "created_by": username,
    }


def issue_run_token(settings: Settings, combined: dict[str, Any], flights: list[str]) -> str:
    exp = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_SECONDS)
    payload = {
        "typ": TOKEN_TYPE,
        "flights": list(flights),
        "exp": exp,
        **{key: round(float(combined[key]), digits) for key, digits in _DIGITS.items()},
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def assert_run_token(settings: Settings, token: str, body: dict[str, Any]) -> None:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "标定结果已失效，请重新标定") from exc
    if claims.get("typ") != TOKEN_TYPE:
        raise HTTPException(400, "标定结果已失效，请重新标定")
    for key, digits in _DIGITS.items():
        if round(float(claims[key]), digits) != round(float(body[key]), digits):
            raise HTTPException(400, "标定结果已失效，请重新标定")
    if list(claims.get("flights") or []) != list(body.get("flights") or []):
        raise HTTPException(400, "标定结果已失效，请重新标定")


def _usable_names(result: dict[str, Any]) -> list[str]:
    names = []
    for item in result.get("flights") or []:
        if not isinstance(item, dict):
            continue
        cal = item.get("calibration") or {}
        if cal.get("usable") and item.get("flight"):
            names.append(str(item["flight"]))
    return names


def present_run(settings: Settings, result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or "combined" not in result:
        detail = result.get("error") if isinstance(result, dict) else None
        raise HTTPException(502, detail or "标定没有返回结果")
    combined = result.get("combined") or {}
    if not combined.get("usable"):
        return result
    names = _usable_names(result)
    if not names:
        raise HTTPException(502, "标定没有返回可用架次")
    return {**result, "run_token": issue_run_token(settings, combined, names)}


def _validate(body: dict[str, Any]) -> None:
    along = float(body["along_offset_m"])
    bore = float(body["boresight_px"])
    if not -20 <= along <= 20:
        raise HTTPException(400, "沿航向偏移超出范围")
    if not -400 <= bore <= 400:
        raise HTTPException(400, "狭缝中心偏置超出范围")
    if float(body["along_se_m"]) < 0 or float(body["boresight_se_px"]) < 0:
        raise HTTPException(400, "不确定度无效")
    flights = body.get("flights")
    if not isinstance(flights, list) or not 1 <= len(flights) <= 8 or any(not isinstance(n, str) or not n.strip() for n in flights):
        raise HTTPException(400, "架次列表无效")


def apply_calibration(db: Session, settings: Settings, user: User, body: dict[str, Any]) -> PushbroomCalibration:
    _validate(body)
    assert_run_token(settings, body["run_token"], body)
    row = PushbroomCalibration(
        id=uuid.uuid4(),
        along_offset_m=round(float(body["along_offset_m"]), 2),
        boresight_px=round(float(body["boresight_px"]), 1),
        along_se_m=round(float(body["along_se_m"]), 2),
        boresight_se_px=round(float(body["boresight_se_px"]), 1),
        flights=list(body["flights"]),
        created_by=user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    record_audit(
        db,
        user.id,
        "pushbroom.calibration.apply",
        {"along_offset_m": row.along_offset_m, "boresight_px": row.boresight_px, "flights": row.flights},
    )
    return row


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
        raise HTTPException(502, "标定超时")
    try:
        data = json.loads(box["body"].decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "标定没有返回结果") from exc
    if not isinstance(data, dict):
        raise HTTPException(502, "标定没有返回结果")
    return data


def run_calibrate(settings: Settings, input_dirs: list[str], *, rpc=None) -> dict[str, Any]:
    if rpc is None:
        if not _online(settings):
            raise HTTPException(409, "推扫计算服务未在线")
        result = _rpc(settings, input_dirs)
    else:
        result = rpc(input_dirs)
    return present_run(settings, result)
