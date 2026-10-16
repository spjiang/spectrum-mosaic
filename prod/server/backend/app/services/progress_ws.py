from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import jobs as jobsvc
from app.services.mq import QUEUE_EVENTS, QUEUE_PROGRESS
from app.config import get_settings

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, job_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms.setdefault(job_id, set()).add(ws)

    def disconnect(self, job_id: str, ws: WebSocket) -> None:
        self._rooms.get(job_id, set()).discard(ws)

    async def broadcast(self, job_id: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._rooms.get(job_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(job_id, ws)


hub = Hub()


def _consume_loop(queue: str, handler) -> None:
    import pika

    settings = get_settings()
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
            ch = conn.channel()
            ch.queue_declare(queue=queue, durable=True)

            def _cb(ch_, method, _props, body):
                try:
                    payload = json.loads(body.decode("utf-8"))
                    db = SessionLocal()
                    try:
                        handler(db, payload)
                    finally:
                        db.close()
                except Exception:  # noqa: BLE001
                    log.exception("consume %s failed", queue)
                ch_.basic_ack(delivery_tag=method.delivery_tag)

            ch.basic_qos(prefetch_count=10)
            ch.basic_consume(queue=queue, on_message_callback=_cb)
            ch.start_consuming()
        except Exception:  # noqa: BLE001
            log.exception("mq consumer reconnecting")
            import time

            time.sleep(3)


def start_consumers(loop: asyncio.AbstractEventLoop) -> None:
    def on_progress(db: Session, payload: dict) -> None:
        job = jobsvc.apply_progress(db, payload)
        if job is not None:
            asyncio.run_coroutine_threadsafe(
                hub.broadcast(str(job.id), {"type": "progress", "job": _job_dict(job)}),
                loop,
            )

    def on_event(db: Session, payload: dict) -> None:
        job = jobsvc.apply_event(db, payload)
        if job is not None:
            asyncio.run_coroutine_threadsafe(
                hub.broadcast(str(job.id), {"type": "event", "job": _job_dict(job), "event": payload}),
                loop,
            )

    import threading

    threading.Thread(target=_consume_loop, args=(QUEUE_PROGRESS, on_progress), daemon=True).start()
    threading.Thread(target=_consume_loop, args=(QUEUE_EVENTS, on_event), daemon=True).start()


def _job_dict(job) -> dict:
    return {
        "id": str(job.id),
        "status": job.status,
        "current_stage": job.current_stage,
        "completed_stage": job.completed_stage,
        "global_percent": job.global_percent,
        "stage_progress": job.stage_progress,
        "message": job.message,
        "eta_seconds": job.eta_seconds,
        "error_summary": job.error_summary,
        "stage_timings": dict(job.stage_timings or {}),
        "stage_steps": dict(getattr(job, "stage_steps", None) or {}),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


async def websocket_job(ws: WebSocket, job_id: str) -> None:
    await hub.connect(job_id, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(job_id, ws)
