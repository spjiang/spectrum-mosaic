from __future__ import annotations

"""立刻停掉任务进程树（含 OpenCV/多进程子进程），不等阶段边界。"""

import os
import signal
import time
from pathlib import Path
from typing import Iterable


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _children(pid: int) -> list[int]:
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    out: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        child = int(entry.name)
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ppid = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split(":", 1)[1].strip().split()[0])
                break
        if ppid == pid:
            out.append(child)
    return out


def descendants(pid: int) -> list[int]:
    tree = [pid]
    stack = [pid]
    seen = {pid}
    while stack:
        cur = stack.pop()
        for child in _children(cur):
            if child in seen:
                continue
            seen.add(child)
            tree.append(child)
            stack.append(child)
    return tree


def _signal_all(pids: Iterable[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            continue


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return ""


def terminate_job_workers(worker_pid: int, timeout: float = 5.0) -> list[int]:
    """杀掉本 Worker 下的计算进程池，不杀 Worker 自己。"""
    if worker_pid <= 0:
        return []
    targets: list[int] = []
    for pid in descendants(worker_pid):
        if pid == worker_pid:
            continue
        cmd = _cmdline(pid)
        if "spawn_main" in cmd or "ms-job-" in cmd:
            targets.append(pid)
    targets.extend(p for p in _orphan_spawn_pids(worker_pid) if p not in targets)
    if not targets:
        return []
    _signal_all(targets, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        targets = [p for p in targets if _alive(p)]
        if not targets:
            return []
        time.sleep(0.1)
    leftovers = [p for p in targets if _alive(p)]
    _signal_all(leftovers, signal.SIGKILL)
    return leftovers


def _orphan_spawn_pids(worker_pid: int) -> list[int]:
    proc = Path("/proc")
    if not proc.is_dir() or worker_pid <= 0:
        return []
    targets: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in {1, worker_pid}:
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ppid = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split(":", 1)[1].strip().split()[0])
                break
        if ppid not in {0, 1}:
            continue
        cmd = _cmdline(pid)
        if "spawn_main" in cmd or "ms-job-" in cmd:
            targets.append(pid)
    return targets


def terminate_orphan_compute(worker_pid: int, timeout: float = 5.0) -> list[int]:
    """杀掉已挂到 init 的计算进程。"""
    return terminate_job_workers(worker_pid, timeout=timeout)


def terminate_process_tree(pid: int, timeout: float = 5.0) -> None:
    """对 pid 及其子孙 SIGTERM，超时则 SIGKILL。"""
    if pid <= 0 or not _alive(pid):
        return
    pids = descendants(pid)
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        _signal_all(pids, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        pids = [p for p in pids if _alive(p)]
        if not pids:
            return
        time.sleep(0.1)
    pids = descendants(pid) if _alive(pid) else [p for p in pids if _alive(p)]
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        _signal_all(pids, signal.SIGKILL)
