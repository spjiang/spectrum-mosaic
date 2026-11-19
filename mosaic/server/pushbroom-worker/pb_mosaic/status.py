"""worker 心跳与当前任务记录。

- status.json：每几秒写一次，给运维和系统监控看（在线、当前任务、内存）。
- current_job.json：子进程开跑时写、结束时删。worker 被重启时据此补报「任务中断」，
  页面不会一直停在运行中。
"""

from __future__ import annotations

import json
import os
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pb_mosaic import __version__

DEFAULT_DIR = "/data/.pushbroom-worker"
HEARTBEAT_INTERVAL_S = 5.0

_lock = threading.Lock()
_state: dict[str, Any] = {"listening": False, "current_job": None, "last_job": None, "license": None}
_started_at = datetime.now(timezone.utc).isoformat()
_stop = threading.Event()


def state_dir() -> Path:
    return Path(os.environ.get("PUSHBROOM_WORKER_STATE_DIR", DEFAULT_DIR))


def status_path() -> Path:
    return state_dir() / "status.json"


def current_job_path() -> Path:
    return state_dir() / "current_job.json"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def set_listening(value: bool) -> None:
    with _lock:
        _state["listening"] = value


def set_license(info: dict[str, Any] | None) -> None:
    with _lock:
        _state["license"] = info


def set_current_job(job: dict[str, Any] | None) -> None:
    """记到心跳里，同时落盘 / 删除 current_job.json。"""
    with _lock:
        if job is None and _state["current_job"] is not None:
            _state["last_job"] = dict(_state["current_job"])
        _state["current_job"] = dict(job) if job else None
    try:
        if job:
            _write_json(current_job_path(), job)
        else:
            current_job_path().unlink(missing_ok=True)
    except OSError:
        pass


def finish_job(result: dict[str, Any]) -> None:
    with _lock:
        if _state["current_job"] is not None:
            _state["last_job"] = {**_state["current_job"], **result}
    set_current_job(None)


def load_interrupted_job() -> dict[str, Any] | None:
    path = current_job_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    return data if isinstance(data, dict) else None


def _cgroup_mb(name: str) -> float | None:
    try:
        raw = (Path("/sys/fs/cgroup") / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        return round(int(raw) / 1024 / 1024, 1)
    except ValueError:
        return None


def snapshot() -> dict[str, Any]:
    with _lock:
        state = json.loads(json.dumps(_state, default=str))
    return {
        "schema_version": 1,
        "service": "pushbroom-worker",
        "version": __version__,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": _started_at,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "queue": "pushbroom.jobs",
        **state,
        "memory": {
            "container_used_mb": _cgroup_mb("memory.current"),
            "container_peak_mb": _cgroup_mb("memory.peak"),
            "container_limit_mb": _cgroup_mb("memory.max"),
            "pb_max_memory_gb": os.environ.get("PB_MAX_MEMORY_GB"),
        },
    }


def write_status() -> None:
    _write_json(status_path(), snapshot())


def _loop() -> None:
    while not _stop.wait(HEARTBEAT_INTERVAL_S):
        try:
            write_status()
        except OSError:
            continue


def start_heartbeat() -> None:
    try:
        write_status()
    except OSError:
        pass
    threading.Thread(target=_loop, name="pb-heartbeat", daemon=True).start()


def stop_heartbeat() -> None:
    _stop.set()
    try:
        write_status()
    except OSError:
        pass
