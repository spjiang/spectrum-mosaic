"""RabbitMQ 投递：长任务期间连接会被心跳掐掉，发布失败不得让算法失败。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable

log = logging.getLogger("ms_mosaic.worker")
logging.getLogger("pika").setLevel(logging.CRITICAL)

QUEUE_JOBS = "mosaic.jobs"
QUEUE_CONTROL = "mosaic.control"
QUEUE_PROGRESS = "mosaic.progress"
QUEUE_EVENTS = "mosaic.events"
QUEUES = (QUEUE_JOBS, QUEUE_CONTROL, QUEUE_PROGRESS, QUEUE_EVENTS)

_HEARTBEAT_S = 30
_IDLE_RECONNECT_S = 20


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
    try:
        import pika
    except ImportError:  # pragma: no cover
        return None
    return pika.BasicProperties(delivery_mode=2, content_type="application/json")


class MqReporter:
    """进度发布走独立连接。计算线程只往队列里丢消息，心跳由发布线程泵。"""

    def __init__(
        self,
        channel,
        job_id: str,
        *,
        rabbitmq_url: str | None = None,
        connect: Callable[[str], Any] | None = None,
        threaded: bool | None = None,
    ) -> None:
        self.ch = channel
        self.conn = None
        self.job_id = job_id
        self.url = rabbitmq_url
        self._connect = connect or connect_amqp
        self._last_ok = 0.0
        self._out: queue.Queue[tuple[str, bytes] | None] = queue.Queue()
        self._stop = threading.Event()
        self._threaded = bool(rabbitmq_url) if threaded is None else threaded
        self._thread: threading.Thread | None = None
        if self._threaded:
            self._thread = threading.Thread(target=self._loop, name="mq-pub", daemon=True)
            self._thread.start()

    def _channel_ok(self) -> bool:
        ch = self.ch
        if ch is None:
            return False
        return bool(getattr(ch, "is_open", True))

    def _drop(self) -> None:
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        self.conn = None
        self.ch = None
        self._last_ok = 0.0

    def _reconnect(self) -> None:
        if not self.url:
            return
        self._drop()
        opened = self._connect(self.url)
        if opened is None:
            return
        self.conn = opened
        self.ch = opened.channel() if hasattr(opened, "channel") else opened
        self._last_ok = time.monotonic()
        log.info("mq publisher reconnected")

    def _stale(self) -> bool:
        if self._last_ok <= 0:
            return not self._channel_ok()
        return (time.monotonic() - self._last_ok) > _IDLE_RECONNECT_S

    def _pump(self) -> None:
        conn = self.conn
        if conn is None:
            return
        try:
            conn.process_data_events(time_limit=0)
        except Exception as exc:  # noqa: BLE001
            log.warning("mq heartbeat lost: %s", exc)
            self._drop()

    def _publish_now(self, queue_name: str, body: bytes) -> None:
        if self._stale() or not self._channel_ok():
            self._reconnect()
        if not self._channel_ok():
            log.warning("mq skip %s: no channel", queue_name)
            return
        self.ch.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=body,
            properties=_properties(),
        )
        self._last_ok = time.monotonic()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                try:
                    item = self._out.get(timeout=0.4)
                except queue.Empty:
                    self._pump()
                    continue
                if item is None:
                    break
                queue_name, body = item
                last_err: Exception | None = None
                for attempt in range(2):
                    try:
                        self._publish_now(queue_name, body)
                        last_err = None
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                        log.warning("mq publish %s failed (attempt %s): %s", queue_name, attempt + 1, exc)
                        self._drop()
                        time.sleep(0.2)
                if last_err is not None:
                    log.warning("mq drop %s after reconnect: %s", queue_name, last_err)
            except Exception as exc:  # noqa: BLE001
                log.warning("mq publisher loop: %s", exc)
                self._drop()
                time.sleep(1)
        while True:
            try:
                item = self._out.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            try:
                self._publish_now(*item)
            except Exception as exc:  # noqa: BLE001
                log.warning("mq flush %s failed: %s", item[0], exc)
        self._drop()

    def _pub(self, queue_name: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"schema_version": 1, "job_id": self.job_id, **payload}, ensure_ascii=False).encode()
        if self._threaded:
            self._out.put((queue_name, body))
            return
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                self._publish_now(queue_name, body)
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("mq publish %s failed (attempt %s): %s", queue_name, attempt + 1, exc)
                self._drop()
        if last_err is not None:
            log.warning("mq drop %s: %s", queue_name, last_err)

    def close(self, timeout: float = 8.0) -> None:
        if not self._threaded:
            self._drop()
            return
        self._stop.set()
        self._out.put(None)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    def stage_start(self, stage_id: str, message: str = "") -> None:
        from ms_mosaic.progress import global_percent_for

        pct = global_percent_for(stage_id, 0)
        log.info("job %s start %s %s", self.job_id, stage_id, message)
        self._pub(
            QUEUE_PROGRESS,
            {"stage_id": stage_id, "stage_progress": 0, "global_percent": pct, "message": message},
        )
        self._pub(QUEUE_EVENTS, {"event": "running", "stage_id": stage_id})

    def progress(
        self,
        stage_id: str,
        stage_progress: float,
        global_percent: float,
        message: str = "",
        eta_seconds: int | None = None,
    ) -> None:
        if message:
            log.info("job %s %s %.0f%% %s", self.job_id, stage_id, global_percent, message)
        self._pub(
            QUEUE_PROGRESS,
            {
                "stage_id": stage_id,
                "stage_progress": stage_progress,
                "global_percent": global_percent,
                "message": message,
                "eta_seconds": eta_seconds,
            },
        )

    def stage_done(self, stage_id: str, elapsed_s: float = 0.0) -> None:
        self._pub(QUEUE_EVENTS, {"event": "stage_done", "stage_id": stage_id, "elapsed_s": elapsed_s})

    def event(self, event: str, **payload: Any) -> None:
        self._pub(QUEUE_EVENTS, {"event": event, **payload})


def wait_while_alive(proc, pump: Callable[[], None], interval: float = 1.0) -> None:
    """等子进程结束，同时泵 AMQP 心跳，避免消费回调卡住导致连接被掐、任务被重投。"""
    while proc.is_alive():
        proc.join(timeout=interval)
        try:
            pump()
        except Exception:  # noqa: BLE001
            log.warning("amqp pump failed while job running", exc_info=True)
            time.sleep(interval)
