from __future__ import annotations

import json
import os
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


def attach_orphan_current_job(heartbeat: dict[str, Any] | None, active: list[dict[str, Any]]) -> dict[str, Any] | None:
    """包装进程已退出时，把还在跑的计算子进程挂回唯一一条 running 任务。"""
    if not heartbeat:
        return heartbeat
    current = heartbeat.get("current_job") or {}
    if current.get("job_id"):
        return heartbeat
    running = [j for j in active if j.get("status") == "running"]
    if len(running) != 1 or not _has_orphan_compute(heartbeat):
        return heartbeat
    job = running[0]
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
        "worker": heartbeat,
        "engine_cpu": os.cpu_count(),
    }
