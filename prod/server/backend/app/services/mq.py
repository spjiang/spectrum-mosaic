from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

import pika

from app.config import get_settings

log = logging.getLogger(__name__)

QUEUE_JOBS = "mosaic.jobs"
QUEUE_CONTROL = "mosaic.control"
QUEUE_PROGRESS = "mosaic.progress"
QUEUE_EVENTS = "mosaic.events"

_RQ_CACHE: tuple[float, str] = (0.0, "unknown")
_RQ_LOCK = threading.Lock()
_RQ_REFRESHING = False
_RQ_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rq-health")
_PROBE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mq-probe")
_PROBE_TIMEOUT_S = 2.0


def _amqp_params(url: str) -> pika.URLParameters:
    params = pika.URLParameters(url)
    params.heartbeat = 30
    params.blocked_connection_timeout = 10
    params.socket_timeout = 10
    params.connection_attempts = 3
    params.retry_delay = 1
    return params


class MQPublisher:
    """每次投递开短连接。pika BlockingConnection 不能在 FastAPI 线程池里闲置复用，
    否则 RabbitMQ 会因 missed heartbeats 掐掉连接，下次投递变成 Connection reset by peer。
    """

    def __init__(self, url: str | None = None) -> None:
        self.url = url or get_settings().rabbitmq_url
        self._lock = threading.Lock()

    def _open(self):
        conn = pika.BlockingConnection(_amqp_params(self.url))
        try:
            ch = conn.channel()
            for q in (QUEUE_JOBS, QUEUE_CONTROL, QUEUE_PROGRESS, QUEUE_EVENTS, f"{QUEUE_JOBS}.dlq"):
                ch.queue_declare(queue=q, durable=True)
            return conn, ch
        except Exception:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            raise

    def _run(self, fn) -> Any:
        last: Exception | None = None
        for attempt in range(2):
            conn = None
            try:
                with self._lock:
                    conn, ch = self._open()
                    try:
                        return fn(ch)
                    finally:
                        try:
                            conn.close()
                        except Exception:  # noqa: BLE001
                            pass
                        conn = None
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.warning("mq operation failed (attempt %s): %s", attempt + 1, exc)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                if attempt == 0:
                    time.sleep(0.2)
        assert last is not None
        raise last

    def publish(self, queue: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def _do(ch) -> None:
            ch.basic_publish(
                exchange="",
                routing_key=queue,
                body=body,
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )

        self._run(_do)

    def publish_job(self, payload: dict[str, Any]) -> None:
        self.publish(QUEUE_JOBS, payload)

    def publish_control(self, payload: dict[str, Any]) -> None:
        self.publish(QUEUE_CONTROL, payload)

    def purge_jobs(self) -> None:
        self._run(lambda ch: ch.queue_purge(QUEUE_JOBS))

    def close(self) -> None:
        return None


def _probe_rabbitmq(url: str) -> str:
    try:
        params = pika.URLParameters(url)
        params.socket_timeout = 1.0
        params.blocked_connection_timeout = 1.0
        params.connection_attempts = 1
        params.retry_delay = 0
        if hasattr(params, "stack_timeout"):
            params.stack_timeout = 1.0
        conn = pika.BlockingConnection(params)
        conn.close()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _refresh_rabbitmq(url: str) -> None:
    global _RQ_CACHE, _RQ_REFRESHING
    try:
        status = _probe_rabbitmq(url)
        with _RQ_LOCK:
            _RQ_CACHE = (time.monotonic(), status)
    finally:
        with _RQ_LOCK:
            _RQ_REFRESHING = False


def _probe_worker(url: str) -> str:
    params = pika.URLParameters(url)
    params.socket_timeout = 1.0
    params.blocked_connection_timeout = 1.0
    params.connection_attempts = 1
    params.retry_delay = 0
    if hasattr(params, "stack_timeout"):
        params.stack_timeout = 1.0
    conn = pika.BlockingConnection(params)
    try:
        ch = conn.channel()
        declared = ch.queue_declare(queue=QUEUE_JOBS, durable=True)
        n = int(declared.method.consumer_count)
    finally:
        conn.close()
    if n >= 1:
        return "ok"
    return "error: 没有 Worker 在听 mosaic.jobs"


def _probe_queue_stats(url: str) -> dict[str, int]:
    params = pika.URLParameters(url)
    params.socket_timeout = 1.0
    params.blocked_connection_timeout = 1.0
    params.connection_attempts = 1
    params.retry_delay = 0
    if hasattr(params, "stack_timeout"):
        params.stack_timeout = 1.0
    conn = pika.BlockingConnection(params)
    try:
        ch = conn.channel()
        declared = ch.queue_declare(queue=QUEUE_JOBS, durable=True)
        return {
            "consumers": int(declared.method.consumer_count),
            "messages": int(declared.method.message_count),
        }
    finally:
        conn.close()


def queue_stats(url: str | None = None) -> dict[str, int | None]:
    u = url or get_settings().rabbitmq_url
    fut = _PROBE_EXECUTOR.submit(_probe_queue_stats, u)
    try:
        return fut.result(timeout=_PROBE_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return {"consumers": None, "messages": None}


def check_worker(url: str | None = None) -> str:
    """mosaic.jobs 上是否至少有一个消费者（compose worker 或本机 run_worker.sh）。"""
    u = url or get_settings().rabbitmq_url
    fut = _PROBE_EXECUTOR.submit(_probe_worker, u)
    try:
        return fut.result(timeout=_PROBE_TIMEOUT_S)
    except FuturesTimeout:
        return "error: Worker 探测超时"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def check_rabbitmq(url: str | None = None) -> str:
    """短缓存；过期时返回旧值并后台刷新，避免 BlockingConnection 拖死整站。"""
    global _RQ_CACHE, _RQ_REFRESHING
    u = url or get_settings().rabbitmq_url
    now = time.monotonic()
    with _RQ_LOCK:
        cached_at, cached_val = _RQ_CACHE
        fresh = cached_val != "unknown" and now - cached_at < 5.0
        if fresh:
            return cached_val
        need_refresh = not _RQ_REFRESHING
        if need_refresh:
            _RQ_REFRESHING = True
        stale = cached_val if cached_val != "unknown" else None

    if stale is not None:
        if need_refresh:
            _RQ_EXECUTOR.submit(_refresh_rabbitmq, u)
        return stale

    # 冷启动：单次探测，最多 1.2s
    try:
        status = _probe_rabbitmq(u)
    except Exception as exc:  # noqa: BLE001
        status = f"error: {exc}"
    with _RQ_LOCK:
        _RQ_CACHE = (time.monotonic(), status)
        _RQ_REFRESHING = False
    return status


class FakeMQPublisher:
    """测试用。"""

    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.controls: list[dict] = []

    def publish_job(self, payload: dict[str, Any]) -> None:
        self.jobs.append(payload)

    def publish_control(self, payload: dict[str, Any]) -> None:
        self.controls.append(payload)

    def purge_jobs(self) -> None:
        self.jobs.clear()

    def close(self) -> None:
        return None
