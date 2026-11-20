"""标定请求单独一条队列，在 worker 进程里跑 calibrate，不占用正在拼图的那条航带任务。"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from typing import Any, Callable

log = logging.getLogger("pb_mosaic.calibrate")

Run = Callable[[list[str], int], dict[str, Any]]


def execute(input_dirs: list[str], *, run: Run) -> dict[str, Any]:
    if not 1 <= len(input_dirs) <= 8:
        return {"error": "架次数量须为 1～8"}
    timeout = min(600, len(input_dirs) * 120)
    try:
        return run(input_dirs, timeout)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
            return {"error": "标定超时"}
        return {"error": str(exc) or exc.__class__.__name__}


def run_cli(input_dirs: list[str], timeout: int) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pb_mosaic", "calibrate"]
    for path in input_dirs:
        cmd.extend(["--input", path])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        err = (proc.stderr or proc.stdout or "标定没有返回结果").strip()
        return {"error": err[-2000:]}
    if not isinstance(data, dict):
        return {"error": "标定没有返回结果"}
    return data


def serve(url: str, stop) -> None:
    """stop 是 threading.Event。连接断开后重试，直到 stop 被置位。"""
    import pika

    from pb_mosaic.mq_pub import QUEUE_CALIBRATE, amqp_params

    while not stop.is_set():
        try:
            conn = pika.BlockingConnection(amqp_params(url))
            ch = conn.channel()
            ch.queue_declare(queue=QUEUE_CALIBRATE, durable=True)
            ch.basic_qos(prefetch_count=1)

            def on_message(ch_, method, props, body: bytes) -> None:
                try:
                    msg = json.loads(body.decode("utf-8"))
                    dirs = msg.get("input_dirs") or []
                    result = execute([str(d) for d in dirs] if isinstance(dirs, list) else [], run=run_cli)
                except Exception as exc:  # noqa: BLE001
                    log.exception("calibrate request failed")
                    result = {"error": str(exc)}
                if getattr(props, "reply_to", None):
                    ch_.basic_publish(
                        exchange="",
                        routing_key=props.reply_to,
                        properties=pika.BasicProperties(correlation_id=props.correlation_id, content_type="application/json"),
                        body=json.dumps(result, ensure_ascii=False).encode("utf-8"),
                    )
                ch_.basic_ack(delivery_tag=method.delivery_tag)

            ch.basic_consume(queue=QUEUE_CALIBRATE, on_message_callback=on_message)
            while not stop.is_set():
                conn.process_data_events(time_limit=1)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("calibrate consumer reconnecting: %s", exc)
            time.sleep(3)
