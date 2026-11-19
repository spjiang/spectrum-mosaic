"""任务子进程：跑一个推扫任务后退出，内存全部还给系统。

  python -m pb_mosaic.job_child <job_payload.json>

收到 SIGTERM（取消）时在下一个检查点停下、删掉半截成果、报「已取消」。
只要发出了结束事件就以 0 退出；非 0 退出由 worker 主进程补报。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from pb_mosaic import __version__
from pb_mosaic.jobfiles import JobMessageError, JobSpec, cancel_requested, mark_done, parse_job
from pb_mosaic.mq_pub import MqReporter, Publisher

log = logging.getLogger("pb_mosaic.job_child")

# 控制文件最短检查间隔
CONTROL_POLL_S = 2.0
EXIT_LICENSE = 3
EXIT_BAD_PAYLOAD = 4


class CancelFlag:
    """SIGTERM 或控制文件都能置位。镶嵌每写一块查一次。"""

    def __init__(self, job: JobSpec) -> None:
        self.job = job
        self._set = threading.Event()
        self._last_poll = 0.0

    def set(self) -> None:
        self._set.set()

    def __call__(self) -> bool:
        if self._set.is_set():
            return True
        now = time.monotonic()
        if now - self._last_poll >= CONTROL_POLL_S:
            self._last_poll = now
            if cancel_requested(self.job):
                self._set.set()
        return self._set.is_set()


def _setup_logging(job: JobSpec) -> logging.Handler:
    job.log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(job.log_dir / "run.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler


def run_job(job: JobSpec, publisher: Publisher) -> str:
    """跑任务并发出结束事件。返回结束状态。"""
    from pb_mosaic.mosaic.merge import Cancelled
    from pb_mosaic.pipeline import PipelineError, run

    reporter = MqReporter(publisher, job.job_id, job.run_attempt)
    flag = CancelFlag(job)
    signal.signal(signal.SIGTERM, lambda *_: flag.set())
    if flag():
        reporter.event("cancelled", message="开跑前已取消")
        mark_done(job, "cancelled")
        return "cancelled"
    log.info("pb_mosaic %s job %s attempt %d", __version__, job.job_id, job.run_attempt)
    log.info("input %s → output %s params %s", job.input_dir, job.output_dir, json.dumps(job.params, ensure_ascii=False))
    reporter.event("started", message=f"推扫计算服务 {__version__} 已开始")
    try:
        result = run(job.input_dir, job.output_dir, job.params, reporter=reporter, cancelled=flag)
    except Cancelled:
        log.info("cancelled")
        reporter.event("cancelled", message="已取消，未完成的成果已删除")
        mark_done(job, "cancelled")
        return "cancelled"
    except PipelineError as exc:
        log.error("failed: %s", exc)
        reporter.event("failed", error=str(exc))
        mark_done(job, "failed", str(exc))
        return "failed"
    except Exception as exc:  # noqa: BLE001
        log.error("internal error\n%s", traceback.format_exc())
        msg = f"内部错误：{type(exc).__name__}: {exc}（详见运行日志）"
        reporter.event("failed", error=msg)
        mark_done(job, "failed", msg)
        return "failed"
    cov = result.report.get("coverage") or {}
    grid = result.report.get("grid") or {}
    msg = f"完成：{grid.get('width')} × {grid.get('height')} 像元，有效覆盖 {cov.get('area_m2', 0):.0f} m²"
    reporter.event("succeeded", message=msg, products=result.products)
    mark_done(job, "succeeded", msg)
    return "succeeded"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(args) != 1:
        log.error("用法：python -m pb_mosaic.job_child <job_payload.json>")
        return 2
    try:
        job = parse_job(json.loads(Path(args[0]).read_text(encoding="utf-8")))
    except (OSError, ValueError, JobMessageError) as exc:
        log.error("任务参数无效：%s", exc)
        return EXIT_BAD_PAYLOAD
    url = os.environ.get("RABBITMQ_URL", "amqp://mosaic:mosaic_secret@127.0.0.1:5672/")
    publisher = Publisher(url)
    handler = _setup_logging(job)
    try:
        from pb_mosaic.license_check import LicenseError, enforce_license_or_raise

        try:
            enforce_license_or_raise()
        except LicenseError as exc:
            log.error("许可校验失败：%s", exc)
            MqReporter(publisher, job.job_id, job.run_attempt).event("failed", error=f"许可校验失败：{exc}")
            mark_done(job, "failed", str(exc))
            return EXIT_LICENSE
        run_job(job, publisher)
        return 0
    finally:
        publisher.close()
        logging.getLogger().removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
