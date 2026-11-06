from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import JobRun
from app.services.mq import QUEUE_JOBS, queue_stats

STALE_AFTER_S = 15.0
ACTIVE_JOB = {"queued", "running", "paused", "awaiting_continue"}


def status_file(settings: Settings) -> Path:
    roots = settings.data_root_list()
    root = Path(roots[0]) if roots else Path("/data")
    return root / ".worker" / "status.json"


def _age_seconds(updated_at: str | None) -> float | None:
    if not updated_at:
        return None
    try:
        raw = updated_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return None


def read_heartbeat(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_holds_job(settings: Settings, job_id: uuid.UUID | str) -> bool:
    """包装进程仍在跑该任务。脱离的计算子进程不算 hold，允许立即暂停 / 清理续跑。"""
    heartbeat = read_heartbeat(status_file(settings))
    if not heartbeat:
        return False
    age = _age_seconds(str(heartbeat.get("updated_at") or ""))
    if age is None or age > STALE_AFTER_S:
        return False
    current = heartbeat.get("current_job") or {}
    if str(current.get("job_id") or "") != str(job_id):
        return False
    if current.get("orphan_compute"):
        return False
    pid = current.get("pid")
    if not pid:
        return False
    procs = heartbeat.get("processes") or []
    if procs:
        live_pids = {p.get("pid") for p in procs if isinstance(p, dict)}
        if pid not in live_pids:
            return False
    return True


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(value)


def _is_worker_helper(proc: dict[str, Any], worker_pid: Any) -> bool:
    pid = proc.get("pid")
    cmd = str(proc.get("cmdline") or "")
    comm = str(proc.get("comm") or "")
    if worker_pid is not None and pid == worker_pid:
        return True
    if "worker_main" in cmd:
        return True
    if "resource_tracker" in cmd or "resource_tracker" in comm:
        return True
    if "docker-init" in cmd or comm in {"docker-init", "tini"}:
        return True
    return False


def _ppid_is_init(ppid: Any, procs: list[dict[str, Any]]) -> bool:
    if ppid in {0, 1, None}:
        return True
    for proc in procs:
        if not isinstance(proc, dict) or proc.get("pid") != ppid:
            continue
        cmd = str(proc.get("cmdline") or "")
        comm = str(proc.get("comm") or "")
        return "docker-init" in cmd or comm in {"docker-init", "tini"}
    return False


def _has_orphan_compute(heartbeat: dict[str, Any]) -> bool:
    """只有挂到 init 的计算进程才算孤儿。挂在 Worker 下面的进程池是正常计算。"""
    worker_pid = heartbeat.get("pid")
    procs = heartbeat.get("processes") or []
    for proc in procs:
        if not isinstance(proc, dict) or _is_worker_helper(proc, worker_pid):
            continue
        ppid = proc.get("ppid")
        if worker_pid is not None and ppid == worker_pid:
            continue
        if _ppid_is_init(ppid, procs):
            return True
    return False


def _proc_map(procs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for proc in procs:
        if not isinstance(proc, dict):
            continue
        try:
            out[int(proc["pid"])] = proc
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _under_worker(pid: int, worker_pid: int | None, proc_map: dict[int, dict[str, Any]]) -> bool:
    if worker_pid is not None and pid == worker_pid:
        return True
    seen: set[int] = set()
    cur = pid
    while cur not in seen:
        if worker_pid is not None and cur == worker_pid:
            return True
        seen.add(cur)
        row = proc_map.get(cur)
        if not row:
            break
        ppid = row.get("ppid")
        if ppid in {None, 0, 1}:
            break
        try:
            cur = int(ppid)
        except (TypeError, ValueError):
            break
    return False


def _find_job_child_pid(heartbeat: dict[str, Any]) -> int | None:
    worker_pid = heartbeat.get("pid")
    proc_map = _proc_map(heartbeat.get("processes") or [])
    for pid, proc in proc_map.items():
        cmd = str(proc.get("cmdline") or "")
        if "ms_mosaic.job_child" not in cmd:
            continue
        if _under_worker(pid, worker_pid, proc_map):
            return pid
    return None


def _has_in_worker_compute(heartbeat: dict[str, Any]) -> bool:
    """Worker 包装还在、计算进程池挂在 Worker 或 job_child 下面（正常正射/空三）。"""
    worker_pid = heartbeat.get("pid")
    proc_map = _proc_map(heartbeat.get("processes") or [])
    child_pid = _find_job_child_pid(heartbeat)
    for proc in proc_map.values():
        if _is_worker_helper(proc, worker_pid):
            continue
        cmd = str(proc.get("cmdline") or "")
        try:
            rss = float(proc.get("rss_mb") or 0)
        except (TypeError, ValueError):
            rss = 0.0
        ppid = proc.get("ppid")
        if ppid == worker_pid and ("spawn_main" in cmd or "ms_mosaic" in cmd or rss >= 50):
            return True
        if child_pid is not None and ppid == child_pid and ("spawn_main" in cmd or rss >= 50):
            return True
    return False


def attach_orphan_current_job(heartbeat: dict[str, Any] | None, active: list[dict[str, Any]]) -> dict[str, Any] | None:
    """心跳里缺 current_job 时，用进程树把唯一一条 running 任务挂回去。"""
    if not heartbeat:
        return heartbeat
    current = heartbeat.get("current_job") or {}
    if current.get("job_id"):
        return heartbeat
    running = [j for j in active if j.get("status") == "running"]
    if len(running) != 1:
        return heartbeat
    job = running[0]
    if _has_orphan_compute(heartbeat):
        merged = dict(heartbeat)
        merged["current_job"] = {
            "job_id": job["id"],
            "pid": None,
            "orphan_compute": True,
            "input_dir": job.get("input_dir"),
            "output_dir": job.get("output_dir"),
            "started_at": job.get("started_at"),
        }
        return merged
    child_pid = _find_job_child_pid(heartbeat)
    mem = (heartbeat.get("memory") or {}).get("job_rss_mb")
    try:
        job_rss = float(mem or 0)
    except (TypeError, ValueError):
        job_rss = 0.0
    if not child_pid and job_rss < 50 and not _has_in_worker_compute(heartbeat):
        return heartbeat
    merged = dict(heartbeat)
    merged["current_job"] = {
        "job_id": job["id"],
        "pid": child_pid or heartbeat.get("pid"),
        "input_dir": job.get("input_dir"),
        "output_dir": job.get("output_dir"),
        "started_at": job.get("started_at"),
    }
    return merged


def _apply_proc_detail(heartbeat: dict[str, Any] | None, settings: Settings) -> dict[str, Any] | None:
    """心跳若还没有 Linux 线程列表，用 proc_detail.json 补上（不杀 Worker）。"""
    if not heartbeat:
        return heartbeat
    procs = heartbeat.get("processes") or []
    if any(isinstance(p, dict) and p.get("tasks") for p in procs):
        return heartbeat
    extra = read_heartbeat(status_file(settings).with_name("proc_detail.json"))
    if not extra:
        return heartbeat
    age = _age_seconds(str(extra.get("updated_at") or ""))
    if age is None or age > 12:
        return heartbeat
    extra_procs = extra.get("processes")
    if not isinstance(extra_procs, list) or not extra_procs:
        return heartbeat
    merged = dict(heartbeat)
    merged["processes"] = extra_procs
    if extra.get("memory"):
        merged["memory"] = extra["memory"]
    return merged


def _compute_state(heartbeat: dict[str, Any] | None, job_id: str) -> str:
    current = (heartbeat or {}).get("current_job") or {}
    if str(current.get("job_id") or "") == str(job_id):
        if current.get("orphan_compute") or not current.get("pid"):
            return "orphan"
        return "live"
    return "stale"


def _fresh_gpu_usage() -> dict[int, dict[str, int]] | None:
    """页面来问时直接读 nvidia-smi。心跳里的显存可能还停在上一分钟。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=index,memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    rows: dict[int, dict[str, int]] = {}
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            rows[int(parts[0])] = {
                "total_mb": int(float(parts[1])),
                "used_mb": int(float(parts[2])),
                "free_mb": int(float(parts[3])),
                "utilization": int(float(parts[4])),
            }
        except ValueError:
            continue
    return rows or None


def _apply_live_gpu(heartbeat: dict[str, Any] | None) -> dict[str, Any] | None:
    if not heartbeat:
        return heartbeat
    gpus = heartbeat.get("gpus")
    devices = gpus.get("devices") if isinstance(gpus, dict) else None
    if not isinstance(devices, list) or not devices:
        return heartbeat
    fresh = _fresh_gpu_usage()
    merged: list[Any] = []
    for device in devices:
        if not isinstance(device, dict):
            merged.append(device)
            continue
        row = dict(device)
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            index = -1
        live = fresh.get(index) if fresh else None
        if live:
            row.update(live)
        elif row.get("used_mb") is None and row.get("total_mb") is not None and row.get("free_mb") is not None:
            row["used_mb"] = max(0, int(row["total_mb"]) - int(row["free_mb"]))
        merged.append(row)
    copied = dict(heartbeat)
    copied["gpus"] = {**gpus, "devices": merged}
    return copied


def inspect_worker(db: Session, settings: Settings) -> dict[str, Any]:
    path = status_file(settings)
    heartbeat = _apply_proc_detail(read_heartbeat(path), settings)
    age = _age_seconds(str(heartbeat.get("updated_at") or "")) if heartbeat else None
    alive = bool(heartbeat) and age is not None and age <= STALE_AFTER_S
    stats = queue_stats(settings.rabbitmq_url)
    consumers = stats.get("consumers")
    if consumers is None:
        mq_status = "error: 队列探测失败"
    elif int(consumers) >= 1:
        mq_status = "ok"
    else:
        mq_status = "error: 没有 Worker 在听 mosaic.jobs"
    jobs = list(
        db.scalars(select(JobRun).where(JobRun.status.in_(tuple(ACTIVE_JOB))).order_by(JobRun.updated_at.desc()))
    )
    active = [
        {
            "id": str(j.id),
            "seq": getattr(j, "seq", None),
            "status": j.status,
            "current_stage": j.current_stage,
            "completed_stage": getattr(j, "completed_stage", None),
            "global_percent": j.global_percent,
            "message": j.message,
            "input_dir": j.input_dir,
            "output_dir": j.output_dir,
            "created_at": _iso(getattr(j, "created_at", None)),
            "started_at": _iso(getattr(j, "started_at", None)),
            "finished_at": _iso(getattr(j, "finished_at", None)),
        }
        for j in jobs
    ]
    heartbeat = attach_orphan_current_job(heartbeat, active)
    for row in active:
        row["compute_state"] = _compute_state(heartbeat, row["id"])
    if alive:
        overall = "ok"
    elif mq_status == "ok":
        overall = "degraded"
    else:
        overall = "down"
    return {
        "status": overall,
        "alive": alive,
        "heartbeat_age_seconds": round(age, 1) if age is not None else None,
        "heartbeat_path": str(path),
        "queue": QUEUE_JOBS,
        "mq_status": mq_status,
        "consumers": stats.get("consumers"),
        "queued_messages": stats.get("messages"),
        "active_jobs": active,
        "worker": _apply_live_gpu(heartbeat),
        "engine_cpu": os.cpu_count(),
    }
