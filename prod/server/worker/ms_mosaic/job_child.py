from __future__ import annotations

"""任务子进程：跑完即退出，把内存还给操作系统。"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from ms_mosaic.control import ControlState
from ms_mosaic.log_io import stamp_print_line
from ms_mosaic.mq_pub import MqReporter

log = logging.getLogger("ms_mosaic.job_child")


def run_stages(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """延迟加载算法，避免 Worker 或单测一 import 就拉起 rasterio。"""
    from ms_mosaic.stage_runner import run_stages as _impl

    return _impl(*args, **kwargs)


class _JobLogTee:
    """把 print / logging 同步写到任务 run.log，供可视化实时拉取。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fp = path.open("a", encoding="utf-8")
        self._handler = logging.FileHandler(path, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self._handler)
        self._stdout = sys.stdout
        self._buf = ""
        sys.stdout = self  # type: ignore[assignment]

    def write(self, s: str) -> int:
        self._stdout.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._fp.write(stamp_print_line(line + "\n"))
        self._fp.flush()
        return len(s)

    def flush(self) -> None:
        self._stdout.flush()
        if self._buf:
            self._fp.write(stamp_print_line(self._buf))
            self._buf = ""
        self._fp.flush()

    def close(self) -> None:
        self.flush()
        sys.stdout = self._stdout
        logging.getLogger().removeHandler(self._handler)
        self._handler.close()
        self._fp.close()


def handle_job(
    channel,
    payload: dict[str, Any],
    *,
    rabbitmq_url: str,
    connect: Callable[[str], Any] | None = None,
) -> None:
    job_id = str(payload["job_id"])
    params = dict(payload.get("params_snapshot") or {})
    out = Path(params["output_dir"])
    ctrl_path = out / "log" / "control.json"
    control = ControlState(path=ctrl_path)
    reporter = MqReporter(channel, job_id, rabbitmq_url=rabbitmq_url, connect=connect)
    job_log = _JobLogTee(out / "log" / "run.log")
    log.info("job %s start input=%s out=%s", job_id, params.get("input_dir"), out)
    try:
        if control.checkpoint_barrier() == "cancel":
            reporter.event("cancelled")
            return
        result = run_stages(Path(params["input_dir"]), out, params=params, reporter=reporter, control=control)
        status = result.get("status", "succeeded")
        if status == "awaiting_continue":
            reporter.event("awaiting_continue", stage_id=result.get("completed_stage"), message=result.get("message"))
        elif status == "failed":
            reporter.event("failed", error=result.get("error"))
        elif status == "cancelled":
            reporter.event("cancelled")
        elif status == "paused":
            reporter.event("paused", stage_id=result.get("completed_stage"), message=result.get("message") or "已暂停，可继续运行")
        else:
            reporter.event("succeeded", n_shots=result.get("n_shots"))
    except Exception as exc:  # noqa: BLE001
        log.exception("job failed")
        reporter.event("failed", error=str(exc))
    finally:
        reporter.close()
        job_log.close()


def main(argv: Sequence[str] | None = None, *, rabbitmq_url: str | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        log.error("用法: python -m ms_mosaic.job_child --ms-job-<id> <payload.json>")
        return 2
    payload_path = Path(args[-1])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    url = rabbitmq_url or os.environ.get("RABBITMQ_URL", "amqp://mosaic:mosaic_secret@127.0.0.1:5672/")
    handle_job(None, payload, rabbitmq_url=url)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
