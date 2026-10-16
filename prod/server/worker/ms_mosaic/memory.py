"""容器可见内存上限（Docker Desktop Memory Limit / cgroup）。"""

from __future__ import annotations

from pathlib import Path


def _meminfo_bytes(key: str) -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def visible_limit_bytes() -> int | None:
    """Worker 真正能用到的内存上限。

    有 compose mem_limit 时读 cgroup memory.max；否则（Mac Docker Desktop
    常见情况）等于虚拟机 MemTotal，也就是 Settings → Resources → Memory
    扣掉内核占用后的值。配得再高也不会超过这个数。
    """
    cgroup_max = Path("/sys/fs/cgroup/memory.max")
    try:
        raw = cgroup_max.read_text(encoding="utf-8").strip()
        if raw not in {"", "max"}:
            return int(raw)
    except (OSError, ValueError):
        pass
    return _meminfo_bytes("MemTotal")


AUTO_LIMIT_FRACTION = 0.9

# 任务上手动指定的内存（GB）。分块并行把它当作上限，仍与此刻剩余内存取较小值。
_job_memory_gb: float | None = None


def set_job_memory_gb(memory_gb: float | None) -> None:
    """记下这次任务的内存预算。0 或空表示没有单独预算。"""
    global _job_memory_gb
    try:
        gb = float(memory_gb) if memory_gb is not None else 0.0
    except (TypeError, ValueError):
        gb = 0.0
    _job_memory_gb = gb if gb > 0 else None


def job_memory_gb() -> float | None:
    return _job_memory_gb


def resolve_memory_budget(memory_gb: float | None) -> int | None:
    """把任务 memory_gb 收成字节预算。

    0/空 = 按 Docker 引擎上限（MemTotal/cgroup max）的 90%，不看瞬时
    MemAvailable。瞬时剩余会被其它容器和残留进程压得很低，空三会被砍成 1 进程。
    超过引擎上限则压到上限。宿主机 CLI 读不到 cgroup 时返回 None，并行只按 CPU。
    """
    try:
        gb = float(memory_gb) if memory_gb is not None else 0.0
    except (TypeError, ValueError):
        gb = 0.0
    limit = visible_limit_bytes()
    if gb <= 0:
        if limit is None:
            return None
        return int(limit * AUTO_LIMIT_FRACTION)
    requested = int(gb * 1024**3)
    if limit is not None and requested > limit:
        return limit
    return requested


def available_bytes() -> int | None:
    return _meminfo_bytes("MemAvailable")


def process_rss_bytes() -> int | None:
    """本进程当前常驻内存。任务预算是总量，已被自己占掉的部分不能再分给工人。"""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def cap_workers(
    requested: int,
    *,
    per_worker_mb: int,
    reserve_mb: int,
    budget_bytes: int | None = None,
    available: int | None = None,
) -> int:
    """按「引擎预算 ∩ 当前剩余」封顶进程数，避免把 Docker 虚拟机打满后被 oom_kill。

    虚拟机里还有别的容器。只看 MemTotal 的 90% 会放出 12 个正射进程，峰值 27GB，
    子进程被杀后整段失败。剩余不够一个工人时退回 1（串行）。
    """
    n = max(1, int(requested or 1))
    if available is None:
        available = available_bytes()
    usable: int | None = None
    if budget_bytes is not None and available is not None:
        usable = min(int(budget_bytes), int(available))
    elif budget_bytes is not None:
        usable = int(budget_bytes)
    elif available is not None:
        usable = int(available)
    if usable is None:
        return n
    leftover = usable - int(reserve_mb) * 1024 * 1024
    if leftover <= 0:
        return 1
    mem_cap = max(1, leftover // (max(1, int(per_worker_mb)) * 1024 * 1024))
    return max(1, min(n, mem_cap))
