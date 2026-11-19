"""推扫 worker：听 pushbroom.jobs / pushbroom.control，任务在子进程里跑。

- 一次只跑一个任务。任务先认领、ack，再拉起子进程；连接断开不会把计算带走。
- 正在跑任务时又来一条：先不 ack 也不退回（prefetch=1，不会再多来），等当前任务
  结束再处理，避免「退回 → 立刻重投 → 再退回」的空转。消费者一直在，后端据此判断在线。
- 同一次运行（job_id + run_attempt）已经有结束标记的重投消息直接丢弃。
- 取消：先 SIGTERM 让子进程在检查点停下并清理半截成果，超时再 SIGKILL 整个进程组。
- 子进程异常退出（被系统杀、崩溃）而没有发出结束事件时，由这里补报失败。
- 启动时发现上次留下的 current_job.json，说明计算中途被重启，补报失败。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from pb_mosaic import __version__, status
from pb_mosaic.jobfiles import JobMessageError, JobSpec, is_done, mark_done, parse_job
from pb_mosaic.mq_pub import QUEUE_CONTROL, QUEUE_DLQ, QUEUE_EVENTS, QUEUE_JOBS, QUEUES, amqp_params, publish_once

log = logging.getLogger("pb_mosaic.worker")

CANCEL_GRACE_S = 20.0
RECONNECT_DELAY_S = 3.0


@dataclass
class Running:
    job: JobSpec
    proc: subprocess.Popen
    cancel_requested: bool = False
    # 非用户原因中止（服务停止、许可失效）时的说明
    abort_reason: str | None = None


class Worker:
    def __init__(self, url: str) -> None:
        self.url = url
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.running: Running | None = None
        # 已 ack、还没跑完的任务数。>0 时新消息先压着不 ack
        self.claimed = 0
        self.incoming: queue.Queue[tuple[JobSpec, dict[str, Any]]] = queue.Queue()

    # ---- 状态 ----

    def busy(self) -> bool:
        with self.lock:
            return self.claimed > 0

    def current(self) -> Running | None:
        with self.lock:
            return self.running

    # ---- 事件 ----

    def report(self, job: JobSpec, event: str, **payload: Any) -> None:
        body = {"job_id": job.job_id, "run_attempt": job.run_attempt, "event": event, **payload}
        for attempt in range(5):
            try:
                publish_once(self.url, QUEUE_EVENTS, body)
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("report %s for %s failed (attempt %d): %s", event, job.job_id, attempt + 1, exc)
                time.sleep(2.0 * (attempt + 1))
        log.error("could not report %s for job %s", event, job.job_id)

    def dead_letter(self, raw: bytes, reason: str) -> None:
        try:
            publish_once(self.url, QUEUE_DLQ, {"reason": reason, "body": raw.decode("utf-8", errors="replace")[:10000]})
        except Exception:  # noqa: BLE001
            log.exception("dead-letter failed")

    # ---- 取消 ----

    def cancel(self, job_id: str | None, run_attempt: int | None) -> None:
        with self.lock:
            run = self.running
            if run is None:
                return
            if job_id and job_id != run.job.job_id:
                return
            if run_attempt and run_attempt != run.job.run_attempt:
                return
            run.cancel_requested = True
            proc = run.proc
        log.info("cancel job %s", job_id or run.job.job_id)
        _signal_group(proc, signal.SIGTERM)
        threading.Thread(target=self._kill_after_grace, args=(run,), daemon=True, name="pb-cancel").start()

    def _kill_after_grace(self, run: Running) -> None:
        try:
            run.proc.wait(timeout=CANCEL_GRACE_S)
        except subprocess.TimeoutExpired:
            log.warning("job %s did not stop in %.0fs, killing", run.job.job_id, CANCEL_GRACE_S)
            _signal_group(run.proc, signal.SIGKILL)

    def abort_current(self, reason: str, *, wait_s: float = 8.0) -> None:
        """立刻杀掉当前任务（服务停止、许可失效），由 run_job 按 reason 报失败。"""
        with self.lock:
            run = self.running
            if run is None:
                return
            run.abort_reason = reason
        _signal_group(run.proc, signal.SIGKILL)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline and self.current() is run:
            time.sleep(0.1)

    # ---- 跑任务 ----

    def run_job(self, job: JobSpec, raw: dict[str, Any]) -> None:
        job.log_dir.mkdir(parents=True, exist_ok=True)
        payload_path = job.log_dir / "job_payload.json"
        payload_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        cmd = [sys.executable, "-m", "pb_mosaic.job_child", str(payload_path)]
        proc = subprocess.Popen(cmd, env=os.environ.copy(), start_new_session=True)
        run = Running(job, proc)
        with self.lock:
            self.running = run
        status.set_current_job({**job.as_dict(), "pid": proc.pid, "started_at": _now()})
        log.info("job %s attempt %d started pid=%d", job.job_id, job.run_attempt, proc.pid)
        try:
            code = proc.wait()
        finally:
            _signal_group(proc, signal.SIGKILL)
            with self.lock:
                self.running = None
        result = "ok"
        if not is_done(job):
            result = self._report_abnormal_exit(run, code)
        status.finish_job({"exit_code": code, "result": result, "finished_at": _now()})
        log.info("job %s finished code=%s", job.job_id, code)

    def _report_abnormal_exit(self, run: Running, code: int) -> str:
        job = run.job
        _remove_partials(job)
        if run.abort_reason:
            self.report(job, "failed", error=run.abort_reason)
            mark_done(job, "failed", run.abort_reason)
            return "aborted"
        if run.cancel_requested:
            self.report(job, "cancelled", message="已取消")
            mark_done(job, "cancelled")
            return "cancelled"
        if code == -signal.SIGKILL:
            msg = (
                "计算进程被系统强制结束（多半是内存不足）。可在部署配置里调小 PB_MAX_MEMORY_GB"
                " 或增加容器内存后重试"
            )
        elif code < 0:
            msg = f"计算进程被信号 {-code} 结束，可重试"
        else:
            msg = f"计算进程异常退出（exit {code}），详见运行日志"
        self.report(job, "failed", error=msg)
        mark_done(job, "failed", msg)
        return "failed"

    def recover_interrupted(self) -> None:
        data = status.load_interrupted_job()
        if not data:
            return
        try:
            job = parse_job({"schema_version": 1, **data})
        except JobMessageError:
            status.set_current_job(None)
            return
        if not is_done(job):
            log.warning("job %s was interrupted by a worker restart", job.job_id)
            _remove_partials(job)
            msg = "推扫计算服务在任务运行中重启，任务中断，可重试"
            self.report(job, "failed", error=msg)
            mark_done(job, "failed", msg)
        status.set_current_job(None)

    def main_loop(self) -> None:
        while not self.stop.is_set():
            try:
                job, raw = self.incoming.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.run_job(job, raw)
            except Exception:  # noqa: BLE001
                log.exception("run job %s crashed", job.job_id)
                if not is_done(job):
                    self.report(job, "failed", error="推扫计算服务内部错误，任务未能启动")
                    mark_done(job, "failed")
            finally:
                with self.lock:
                    self.claimed -= 1
        self._fail_unstarted()

    def _fail_unstarted(self) -> None:
        """已 ack 但没来得及开跑的任务，停机时报失败，不让页面一直排队。"""
        while True:
            try:
                job, _raw = self.incoming.get_nowait()
            except queue.Empty:
                return
            msg = "推扫计算服务停止，任务未开始，可重试"
            self.report(job, "failed", error=msg)
            mark_done(job, "failed", msg)
            with self.lock:
                self.claimed -= 1

    # ---- 队列 ----

    def consume_jobs(self) -> None:
        import pika

        while not self.stop.is_set():
            held: list[tuple[int, bytes]] = []
            try:
                conn = pika.BlockingConnection(amqp_params(self.url))
                ch = conn.channel()
                for q in QUEUES:
                    ch.queue_declare(queue=q, durable=True)
                ch.basic_qos(prefetch_count=1)

                def on_job(ch_, method, _props, body: bytes) -> None:
                    held.append((method.delivery_tag, body))

                ch.basic_consume(queue=QUEUE_JOBS, on_message_callback=on_job)
                status.set_listening(True)
                log.info("listening on %s", QUEUE_JOBS)
                while not self.stop.is_set():
                    conn.process_data_events(time_limit=1)
                    if held and not self.busy():
                        tag, body = held.pop(0)
                        self._take(body)
                        ch.basic_ack(delivery_tag=tag)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                status.set_listening(False)
                log.warning("job consumer reconnecting: %s", exc)
                time.sleep(RECONNECT_DELAY_S)

    def _take(self, body: bytes) -> None:
        try:
            raw = json.loads(body.decode("utf-8"))
            job = parse_job(raw)
        except (ValueError, JobMessageError) as exc:
            log.error("bad job message: %s", exc)
            self.dead_letter(body, str(exc))
            return
        if is_done(job):
            log.info("skip finished job %s attempt %d", job.job_id, job.run_attempt)
            return
        with self.lock:
            self.claimed += 1
        self.incoming.put((job, raw))

    def consume_control(self) -> None:
        import pika

        while not self.stop.is_set():
            try:
                conn = pika.BlockingConnection(amqp_params(self.url))
                ch = conn.channel()
                ch.queue_declare(queue=QUEUE_CONTROL, durable=True)

                def on_control(ch_, method, _props, body: bytes) -> None:
                    try:
                        msg = json.loads(body.decode("utf-8"))
                        if str(msg.get("action") or "").lower() == "cancel":
                            attempt = msg.get("run_attempt")
                            self.cancel(msg.get("job_id"), int(attempt) if attempt else None)
                    except Exception:  # noqa: BLE001
                        log.exception("control message failed")
                    finally:
                        ch_.basic_ack(delivery_tag=method.delivery_tag)

                ch.basic_consume(queue=QUEUE_CONTROL, on_message_callback=on_control)
                while not self.stop.is_set():
                    conn.process_data_events(time_limit=1)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                log.warning("control consumer reconnecting: %s", exc)
                time.sleep(RECONNECT_DELAY_S)


def _remove_partials(job: JobSpec) -> None:
    """子进程被强杀时留下的 .part 半截文件动辄几十 GB，删掉。"""
    for sub in ("products", "process", "report"):
        d = job.output_dir / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.part"):
            try:
                p.unlink()
                log.info("removed partial %s", p)
            except OSError:
                log.warning("could not remove %s", p)


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _license_required() -> bool:
    return os.environ.get("MOSAIC_REQUIRE_LICENSE", "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from pb_mosaic.license_check import LicenseError, enforce_license_or_raise, lease_check_interval_seconds

    try:
        enforce_license_or_raise()
    except LicenseError as exc:
        log.error("许可校验失败：%s", exc)
        return 2
    status.set_license({"required": _license_required(), "ok": True, "checked_at": _now()})
    url = os.environ.get("RABBITMQ_URL", "amqp://mosaic:mosaic_secret@127.0.0.1:5672/")
    worker = Worker(url)
    status.start_heartbeat()
    worker.recover_interrupted()

    def lease_supervisor() -> None:
        interval = lease_check_interval_seconds()
        while not worker.stop.wait(interval):
            try:
                enforce_license_or_raise()
                status.set_license({"required": True, "ok": True, "checked_at": _now()})
            except LicenseError as exc:
                log.error("许可复查失败，停止接任务：%s", exc)
                status.set_license({"required": True, "ok": False, "error": str(exc), "checked_at": _now()})
                status.set_listening(False)
                worker.stop.set()
                worker.abort_current(f"许可复查失败，任务中止：{exc}")
                status.stop_heartbeat()
                os._exit(2)

    if _license_required():
        threading.Thread(target=lease_supervisor, daemon=True, name="pb-license").start()
    threading.Thread(target=worker.consume_control, daemon=True, name="pb-control").start()
    threading.Thread(target=worker.consume_jobs, daemon=True, name="pb-jobs").start()

    def on_term(*_: Any) -> None:
        worker.stop.set()
        # 信号在主线程里处理，主线程可能正持有 worker.lock，这里不能再拿锁
        run = worker.running
        if run is not None:
            run.abort_reason = "推扫计算服务停止，任务中断，可重试"
            _signal_group(run.proc, signal.SIGKILL)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    log.info("pushbroom worker %s ready", __version__)
    worker.main_loop()
    status.set_listening(False)
    status.stop_heartbeat()
    log.info("pushbroom worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
