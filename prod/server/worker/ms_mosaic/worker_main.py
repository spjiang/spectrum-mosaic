from __future__ import annotations

"""RabbitMQ worker：只听队列，拼图在独立子进程里跑。

任务先 ack 再拉起子进程，队列断开不会把计算带走。子进程退出后操作系统收回
全部内存，Worker 自身保持空闲基线。可视化 kill 杀任务子进程树，不杀 Worker。
"""

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ms_mosaic.job_proc import terminate_job_workers, terminate_process_tree
from ms_mosaic.mq_pub import QUEUES, amqp_params, connect_amqp
from ms_mosaic import worker_status

log = logging.getLogger("ms_mosaic.worker")

QUEUE_JOBS = "mosaic.jobs"
QUEUE_CONTROL = "mosaic.control"
QUEUE_PROGRESS = "mosaic.progress"
QUEUE_EVENTS = "mosaic.events"

_job_lock = threading.Lock()
_job_id: str | None = None
_job_active = False
_job_child_pid: int | None = None
_incoming: queue.Queue[dict[str, Any]] = queue.Queue()


def job_child_command(job_id: str, payload_path: Path) -> list[str]:
    """命令行带 --ms-job-。不导入 job_child，避免 Worker 主进程加载算法库。"""
    return [sys.executable, "-m", "ms_mosaic.job_child", f"--ms-job-{job_id}", str(payload_path)]


def _job_running() -> bool:
    with _job_lock:
        return _job_active


def _spawn_job(url: str, payload: dict[str, Any]) -> subprocess.Popen:
    """把任务参数落到文件，再拉起带 --ms-job- 标记的子进程。"""
    job_id = str(payload["job_id"])
    params = dict(payload.get("params_snapshot") or {})
    out = Path(params["output_dir"])
    log_dir = out / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload_path = log_dir / "job_payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    env["RABBITMQ_URL"] = url
    cmd = job_child_command(job_id, payload_path)
    log.info("spawn job child: %s", " ".join(cmd))
    return subprocess.Popen(cmd, env=env, start_new_session=True)


def kill_current_job() -> str | None:
    """立刻杀掉当前任务子进程树，Worker 本身继续听队列。"""
    with _job_lock:
        jid = _job_id
        child = _job_child_pid
    log.info("killing job %s child=%s", jid, child)
    if child:
        terminate_process_tree(child)
    leftover = terminate_job_workers(os.getpid())
    if leftover:
        log.info("compute still alive after kill: %s", leftover)
    return jid


def delivery_action(current_id: str | None, current_alive: bool, incoming_id: str) -> str:
    """同一任务重投时忽略；另有任务在跑则退回队列。"""
    if current_alive and current_id:
        if current_id == incoming_id:
            return "ignore"
        return "busy"
    return "run"


def _ack_quiet(channel, method) -> None:
    try:
        channel.basic_ack(delivery_tag=method.delivery_tag)
    except Exception:  # noqa: BLE001
        log.exception("ack failed")


def _nack_requeue(channel, method) -> None:
    try:
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception:  # noqa: BLE001
        log.exception("nack failed")


def _control_matches(msg: dict[str, Any], current: str | None) -> bool:
    action = str(msg.get("action") or "").lower()
    if action == "kill_all":
        return True
    if action not in {"cancel", "kill"}:
        return False
    jid = msg.get("job_id")
    if not jid:
        return True
    return current is not None and str(jid) == current


def _control_supervisor(url: str, stop: threading.Event) -> None:
    import pika

    while not stop.is_set():
        try:
            conn = pika.BlockingConnection(amqp_params(url))
            ch = conn.channel()
            ch.queue_declare(queue=QUEUE_CONTROL, durable=True)

            def _cb(ch_, method, _props, body):
                try:
                    msg = json.loads(body.decode("utf-8"))
                    with _job_lock:
                        current = _job_id
                    if _control_matches(msg, current):
                        kill_current_job()
                except Exception:  # noqa: BLE001
                    log.exception("control handler failed")
                finally:
                    ch_.basic_ack(delivery_tag=method.delivery_tag)

            ch.basic_consume(queue=QUEUE_CONTROL, on_message_callback=_cb)
            while not stop.is_set():
                conn.process_data_events(time_limit=1)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            log.exception("control supervisor reconnecting")
            time.sleep(2)


def _claim_job(job_id: str) -> str:
    global _job_id, _job_active
    with _job_lock:
        action = delivery_action(_job_id, _job_active, job_id)
        if action == "run":
            _job_id = job_id
            _job_active = True
        return action


def _release_job(job_id: str) -> None:
    global _job_id, _job_active
    with _job_lock:
        if _job_id == job_id:
            _job_id = None
            _job_active = False


def _consume_jobs(url: str, stop: threading.Event) -> None:
    """独立连接消费任务。先认领再 ack，通道断开不影响主线程里的计算。"""
    import pika

    while not stop.is_set():
        try:
            conn = pika.BlockingConnection(amqp_params(url))
            ch = conn.channel()
            for q in QUEUES:
                ch.queue_declare(queue=q, durable=True)
            ch.basic_qos(prefetch_count=1)
            worker_status.set_listening(True)

            def _on_job(ch_, method, _props, body):
                payload = json.loads(body.decode("utf-8"))
                job_id = str(payload.get("job_id") or "")
                log.info("job %s", job_id)
                action = _claim_job(job_id)
                if action == "ignore":
                    log.info("ignore redelivery of in-flight job %s", job_id)
                    _ack_quiet(ch_, method)
                    return
                if action == "busy":
                    log.warning("busy with current job, requeue %s", job_id)
                    _nack_requeue(ch_, method)
                    return
                _incoming.put(payload)
                _ack_quiet(ch_, method)

            ch.basic_consume(queue=QUEUE_JOBS, on_message_callback=_on_job)
            log.info("worker listening on %s", QUEUE_JOBS)
            while not stop.is_set():
                conn.process_data_events(time_limit=1)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            worker_status.set_listening(False)
            log.exception("job consumer reconnecting")
            time.sleep(2)


def _crash_message(code: int) -> str:
    if code == -9:
        return "计算进程被系统杀掉（内存不足，exit -9）。已完成的阶段仍保留，可失败重试。"
    if code < 0:
        return f"计算进程被信号结束（exit {code}）。已完成的阶段仍保留，可失败重试。"
    return f"计算进程异常退出（exit {code}）。已完成的阶段仍保留，可失败重试。"


def _report_child_crash(url: str, job_id: str, code: int) -> None:
    """子进程被信号杀掉时发不出失败事件，页面会一直停在运行中。这里补报一次。"""
    if code in (0, None):
        return
    body = json.dumps(
        {"schema_version": 1, "job_id": job_id, "event": "failed", "error": _crash_message(code)},
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        conn = connect_amqp(url)
        try:
            channel = conn.channel()
            channel.basic_publish(exchange="", routing_key=QUEUE_EVENTS, body=body)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        log.exception("report child crash failed job=%s code=%s", job_id, code)


def _run_payload(url: str, payload: dict[str, Any]) -> None:
    """拉起任务子进程并等待结束。心跳 pid 是子进程，便于和 Worker 自身内存分开。"""
    global _job_id, _job_active, _job_child_pid
    job_id = str(payload.get("job_id") or "")
    proc = _spawn_job(url, payload)
    with _job_lock:
        _job_id = job_id
        _job_active = True
        _job_child_pid = proc.pid
    worker_status.set_current_job(
        {
            "job_id": job_id,
            "pid": proc.pid,
            "input_dir": (payload.get("params_snapshot") or {}).get("input_dir"),
            "output_dir": (payload.get("params_snapshot") or {}).get("output_dir"),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    try:
        code = proc.wait()
        if code not in (0, None):
            log.warning("job child %s exit %s", job_id, code)
            _report_child_crash(url, job_id, int(code))
    finally:
        if proc.poll() is None:
            terminate_process_tree(proc.pid)
        leftover = terminate_job_workers(os.getpid())
        if leftover:
            log.warning("compute leftover after job %s: %s", job_id, leftover)
        with _job_lock:
            if _job_id == job_id:
                _job_id = None
                _job_active = False
            _job_child_pid = None
        worker_status.set_current_job(None, remember=False)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    url = os.environ.get("RABBITMQ_URL", "amqp://mosaic:mosaic_secret@127.0.0.1:5672/")
    leftover = terminate_job_workers(os.getpid())
    if leftover:
        log.warning("startup leftover compute: %s", leftover)
    worker_status.start_heartbeat()
    stop = threading.Event()
    threading.Thread(target=_control_supervisor, args=(url, stop), daemon=True, name="worker-control").start()
    threading.Thread(target=_consume_jobs, args=(url, stop), daemon=True, name="worker-jobs").start()
    worker_status.set_listening(True)
    log.info("worker ready, jobs run in child process")
    try:
        while True:
            try:
                payload = _incoming.get(timeout=1.0)
            except queue.Empty:
                continue
            _run_payload(url, payload)
    except KeyboardInterrupt:
        stop.set()
        worker_status.set_listening(False)
        kill_current_job()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
