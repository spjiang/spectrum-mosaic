"""RabbitMQ：队列名、连接参数，以及任务子进程回报进度和事件的发布器。

计算线程只把消息丢进内存队列，由发布线程发送并泵心跳。长时间计算中连接被掐掉时
自动重连；发不出去只记日志，不让计算失败。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import warnings
from typing import Any, Callable

from pb_mosaic.stages import global_percent

log = logging.getLogger("pb_mosaic.mq")
logging.getLogger("pika").setLevel(logging.WARNING)

# pika 1.5 起每建一个 BlockingConnection 都打一条弃用告警；2.0 之前接口不变
warnings.filterwarnings("ignore", message="BlockingConnection is deprecated", category=DeprecationWarning)

QUEUE_JOBS = "pushbroom.jobs"
QUEUE_CONTROL = "pushbroom.control"
QUEUE_PROGRESS = "pushbroom.progress"
QUEUE_EVENTS = "pushbroom.events"
QUEUE_DLQ = "pushbroom.jobs.dlq"
QUEUE_CALIBRATE = "pushbroom.calibrate"
QUEUES = (QUEUE_JOBS, QUEUE_CONTROL, QUEUE_PROGRESS, QUEUE_EVENTS, QUEUE_DLQ, QUEUE_CALIBRATE)
SCHEMA_VERSION = 1

_HEARTBEAT_S = 30
_IDLE_RECONNECT_S = 20
_PUBLISH_ATTEMPTS = 3


def amqp_params(url: str):
    import pika

    params = pika.URLParameters(url)
    params.heartbeat = _HEARTBEAT_S
    params.blocked_connection_timeout = 300
    params.socket_timeout = 15
    params.connection_attempts = 3
    params.retry_delay = 1
    return params


def connect_amqp(url: str):
    import pika

    conn = pika.BlockingConnection(amqp_params(url))
    ch = conn.channel()
    for q in QUEUES:
        ch.queue_declare(queue=q, durable=True)
    return conn


def _properties():
    import pika

    return pika.BasicProperties(delivery_mode=2, content_type="application/json")


def publish_once(url: str, queue_name: str, payload: dict[str, Any]) -> None:
    """单发一条（worker 主进程补报失败用）。失败抛异常，由调用方决定怎么办。"""
    body = json.dumps({"schema_version": SCHEMA_VERSION, **payload}, ensure_ascii=False).encode("utf-8")
    conn = connect_amqp(url)
    try:
        conn.channel().basic_publish(exchange="", routing_key=queue_name, body=body, properties=_properties())
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


class Publisher:
    """后台线程发布。connect 可替换，便于测试。"""

    def __init__(self, url: str, *, connect: Callable[[str], Any] | None = None) -> None:
        self.url = url
        self._connect = connect or connect_amqp
        self.conn = None
        self.ch = None
        self._last_ok = 0.0
        self._out: queue.Queue[tuple[str, bytes] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="pb-mq-pub", daemon=True)
        self._thread.start()

    def publish(self, queue_name: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"schema_version": SCHEMA_VERSION, **payload}, ensure_ascii=False).encode("utf-8")
        self._out.put((queue_name, body))

    def close(self, timeout: float = 10.0) -> None:
        self._out.put(None)
        self._thread.join(timeout=timeout)

    def _drop(self) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        self.conn = None
        self.ch = None
        self._last_ok = 0.0

    def _ensure(self) -> None:
        stale = self._last_ok and time.monotonic() - self._last_ok > _IDLE_RECONNECT_S
        if self.ch is None or not getattr(self.ch, "is_open", True) or stale:
            self._drop()
            self.conn = self._connect(self.url)
            self.ch = self.conn.channel()
            self._last_ok = time.monotonic()

    def _send(self, queue_name: str, body: bytes) -> None:
        for attempt in range(_PUBLISH_ATTEMPTS):
            try:
                self._ensure()
                self.ch.basic_publish(exchange="", routing_key=queue_name, body=body, properties=_properties())
                self._last_ok = time.monotonic()
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("publish %s failed (attempt %d): %s", queue_name, attempt + 1, exc)
                self._drop()
                time.sleep(min(2.0, 0.3 * (attempt + 1)))
        log.error("drop message to %s after %d attempts", queue_name, _PUBLISH_ATTEMPTS)

    def _pump(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.process_data_events(time_limit=0)
            self._last_ok = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            log.warning("mq heartbeat lost: %s", exc)
            self._drop()

    def _loop(self) -> None:
        while True:
            try:
                item = self._out.get(timeout=0.5)
            except queue.Empty:
                self._pump()
                continue
            if item is None:
                break
            self._send(*item)
        self._drop()


class MqReporter:
    """pipeline.Reporter 的队列实现：进度发 pushbroom.progress，阶段完成和日志发 pushbroom.events。"""

    def __init__(self, publisher: Publisher, job_id: str, run_attempt: int) -> None:
        self.pub = publisher
        self.base = {"job_id": job_id, "run_attempt": run_attempt}

    def progress(self, stage: str, fraction: float, message: str) -> None:
        self.pub.publish(
            QUEUE_PROGRESS,
            {
                **self.base,
                "stage_id": stage,
                "stage_progress": round(max(0.0, min(1.0, fraction)) * 100.0, 2),
                "global_percent": global_percent(stage, fraction),
                "message": message,
            },
        )

    def stage_done(self, stage: str, elapsed_s: float, message: str) -> None:
        self.pub.publish(
            QUEUE_EVENTS,
            {**self.base, "event": "stage_done", "stage_id": stage, "elapsed_s": round(elapsed_s, 2), "message": message},
        )
        self.progress(stage, 1.0, message)

    def log(self, message: str, level: str = "info") -> None:
        self.pub.publish(QUEUE_EVENTS, {**self.base, "event": "log", "level": level, "message": message})

    def event(self, event: str, **payload: Any) -> None:
        self.pub.publish(QUEUE_EVENTS, {**self.base, "event": event, **payload})
