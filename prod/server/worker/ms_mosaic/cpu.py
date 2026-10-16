"""任务 CPU 预算（对应 Docker Desktop CPU limit，不是 macOS 宿主机全部核）。"""

from __future__ import annotations

import os
from pathlib import Path

_job_cpus: int | None = None


def parse_cpu_max(raw: str) -> int | None:
    """cgroup v2 cpu.max → 可见核数。max 表示未单独限核。"""
    text = (raw or "").strip()
    if not text or text.startswith("max"):
        return None
    parts = text.split()
    try:
        quota = int(parts[0])
        period = int(parts[1]) if len(parts) > 1 else 100_000
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, quota // period)


def visible_cpu_count() -> int:
    path = Path("/sys/fs/cgroup/cpu.max")
    try:
        n = parse_cpu_max(path.read_text(encoding="utf-8"))
        if n:
            return n
    except OSError:
        pass
    return max(1, os.cpu_count() or 1)


def resolve_cpu_budget(cpus: int | float | None) -> int | None:
    try:
        n = int(cpus) if cpus is not None else 0
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return None
    return max(1, min(n, visible_cpu_count()))


def set_job_cpus(n: int | None) -> None:
    global _job_cpus
    _job_cpus = n


def job_cpus() -> int | None:
    return _job_cpus


def cpu_cap(requested: int | None = None) -> int:
    visible = visible_cpu_count()
    budget = job_cpus()
    if budget is not None and budget > 0:
        cap = max(1, min(int(budget), visible))
    else:
        cap = max(1, visible - 1 if visible > 1 else 1)
    if requested is None:
        return cap
    return max(1, min(int(requested), cap))
