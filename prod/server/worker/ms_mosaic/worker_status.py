from __future__ import annotations

"""Worker 心跳：把进程树和当前任务写到 /data/.worker/status.json，给可视化读取。"""

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_listening = False
_current_job: dict[str, Any] | None = None
_last_job: dict[str, Any] | None = None
_started_at = datetime.now(timezone.utc).isoformat()
_stop = threading.Event()


def status_path() -> Path:
    raw = os.environ.get("WORKER_STATUS_PATH", "/data/.worker/status.json")
    return Path(raw)


def set_listening(value: bool) -> None:
    global _listening
    with _lock:
        _listening = value


def set_current_job(job: dict[str, Any] | None, *, remember: bool = True) -> None:
    global _current_job, _last_job
    with _lock:
        if job:
            _current_job = dict(job)
            if remember:
                _last_job = dict(job)
        else:
            _current_job = None
            if not remember:
                _last_job = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmdline(pid: int) -> str:
    path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = path.read_bytes().replace(b"\x00", b" ").strip()
        return raw.decode("utf-8", errors="replace") or f"pid={pid}"
    except OSError:
        return f"pid={pid}"


def _status_map(pid: int) -> dict[str, str]:
    out: dict[str, str] = {}
    path = Path(f"/proc/{pid}/status")
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _ppid_of(pid: int) -> int | None:
    st = _status_map(pid)
    raw = st.get("PPid")
    if raw is None:
        return None
    try:
        return int(raw.split()[0])
    except ValueError:
        return None


def _int_field(st: dict[str, str], key: str, default: int = 0) -> int:
    raw = (st.get(key) or str(default)).split()[0]
    try:
        return int(raw)
    except ValueError:
        return default


def _proc_row(pid: int) -> dict[str, Any] | None:
    st = _status_map(pid)
    if not st and not Path(f"/proc/{pid}").exists():
        return None
    return {
        "pid": pid,
        "ppid": _int_field(st, "PPid") if st.get("PPid") else _ppid_of(pid),
        "state": (st.get("State") or "?").split()[0],
        "threads": _int_field(st, "Threads"),
        "rss_mb": round(_int_field(st, "VmRSS") / 1024, 1),
        "cmdline": _cmdline(pid),
        "comm": (st.get("Name") or "").strip(),
        "tasks": _task_rows(pid),
    }


def _task_rows(pid: int) -> list[dict[str, Any]]:
    """Linux 线程：/proc/<pid>/task/<tid>。主线程 tid==pid，其余是子线程。"""
    task_dir = Path(f"/proc/{pid}/task")
    if not task_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    try:
        tids = sorted(int(p.name) for p in task_dir.iterdir() if p.name.isdigit())
    except OSError:
        return []
    for tid in tids:
        comm = ""
        state = "?"
        try:
            comm = Path(f"/proc/{pid}/task/{tid}/comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            comm = ""
        st = _status_map(tid) if tid == pid else {}
        if not st:
            try:
                for line in Path(f"/proc/{pid}/task/{tid}/status").read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("State:"):
                        state = line.split(":", 1)[1].strip().split()[0]
                        break
            except OSError:
                state = "?"
        else:
            state = (st.get("State") or "?").split()[0]
        rows.append({"tid": tid, "comm": comm or f"tid={tid}", "state": state, "main": tid == pid})
    return rows


def collect_processes(root_pid: int | None = None) -> list[dict[str, Any]]:
    """容器 PID 命名空间内全部进程，按父子树排序（含孙进程）。"""
    root = root_pid or os.getpid()
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return [
            {
                "pid": root,
                "ppid": os.getppid(),
                "state": "?",
                "threads": threading.active_count(),
                "rss_mb": None,
                "cmdline": " ".join(sys.argv) or f"pid={root}",
                "comm": "",
                "depth": 0,
                "child_count": 0,
                "tasks": [],
            }
        ]

    rows_by: dict[int, dict[str, Any]] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        row = _proc_row(int(entry.name))
        if row is not None:
            rows_by[row["pid"]] = row

    kids: dict[int, list[int]] = {}
    for pid, row in rows_by.items():
        ppid = row.get("ppid")
        if isinstance(ppid, int) and ppid in rows_by and ppid != pid:
            kids.setdefault(ppid, []).append(pid)

    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(pid: int, depth: int) -> None:
        if pid in seen or pid not in rows_by:
            return
        seen.add(pid)
        children = sorted(kids.get(pid, []))
        rec = dict(rows_by[pid])
        rec["depth"] = depth
        rec["child_count"] = len(children)
        ordered.append(rec)
        for child in children:
            walk(child, depth + 1)

    start = 1 if 1 in rows_by else root
    walk(start, 0)
    if root not in seen:
        walk(root, 0)
    for pid in sorted(rows_by):
        if pid not in seen:
            walk(pid, 0)
    return ordered


def _rss_mb(proc: dict[str, Any]) -> float:
    raw = proc.get("rss_mb")
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _is_helper(cmdline: str) -> bool:
    return "resource_tracker" in (cmdline or "")


def _has_detached_compute(processes: list[dict[str, Any]], root_pid: int) -> bool:
    """计算进程已经挂到 init，而不是 Worker 的子进程。"""
    for proc in processes:
        if proc.get("pid") == root_pid or _is_helper(str(proc.get("cmdline") or "")):
            continue
        ppid = proc.get("ppid")
        if ppid == root_pid:
            continue
        if ppid not in {0, 1, None}:
            continue
        cmd = str(proc.get("cmdline") or "")
        if "spawn_main" in cmd or _rss_mb(proc) >= 50:
            return True
    return False


def _cgroup_bytes(name: str) -> int | None:
    path = Path("/sys/fs/cgroup") / name
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw in {"", "max"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def collect_cgroup_memory() -> dict[str, float | None]:
    def to_mb(n: int | None) -> float | None:
        return round(n / 1024 / 1024, 1) if n is not None else None

    return {
        "container_used_mb": to_mb(_cgroup_bytes("memory.current")),
        "container_peak_mb": to_mb(_cgroup_bytes("memory.peak")),
        "container_limit_mb": to_mb(_cgroup_bytes("memory.max")),
    }


def _host_memory() -> dict[str, float | None]:
    """容器可见内存（Docker Desktop 上等于引擎分配，不是 macOS 宿主机全部）。"""
    info: dict[str, float | None] = {"mem_total_mb": None, "mem_available_mb": None}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                info["mem_total_mb"] = round(int(line.split()[1]) / 1024, 1)
            elif line.startswith("MemAvailable:"):
                info["mem_available_mb"] = round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError):
        return info
    return info


def summarize_memory(
    processes: list[dict[str, Any]],
    root_pid: int,
    extra: dict[str, float | None] | None = None,
) -> dict[str, float | None]:
    """空闲时 job_rss_mb 为 0，Worker 自身占用仍会返回。"""
    worker = helper = job = 0.0
    for proc in processes:
        rss = _rss_mb(proc)
        if proc.get("pid") == root_pid:
            worker = rss
        elif _is_helper(str(proc.get("cmdline") or "")):
            helper += rss
        else:
            job += rss
    out: dict[str, float | None] = {
        "worker_rss_mb": round(worker, 1),
        "helper_rss_mb": round(helper, 1),
        "job_rss_mb": round(job, 1),
        "tree_rss_mb": round(worker + helper + job, 1),
        "container_used_mb": None,
        "container_peak_mb": None,
        "container_limit_mb": None,
    }
    if extra:
        out.update(extra)
    return out


def collect_threads() -> list[dict[str, Any]]:
    rows = []
    for t in threading.enumerate():
        rows.append({"name": t.name, "ident": t.ident, "daemon": t.daemon, "alive": t.is_alive()})
    return rows


def snapshot() -> dict[str, Any]:
    with _lock:
        listening = _listening
        job = dict(_current_job) if _current_job else None
        last = dict(_last_job) if _last_job else None
        started = _started_at
    pid = os.getpid()
    processes = collect_processes(pid)
    memory = summarize_memory(processes, pid, collect_cgroup_memory())
    if job and job.get("pid") == pid:
        job.pop("orphan_compute", None)
    elif job is None and last and _has_detached_compute(processes, pid):
        job = dict(last)
        job["pid"] = None
        job["orphan_compute"] = True
    return {
        "schema_version": 1,
        "updated_at": _utc_now(),
        "started_at": started,
        "hostname": socket.gethostname(),
        "pid": pid,
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "listening": listening,
        "queue": "mosaic.jobs",
        "current_job": job,
        "thread_count": threading.active_count(),
        "threads": collect_threads(),
        "processes": processes,
        "memory": memory,
        "host_memory": _host_memory(),
    }


def write_status(path: Path | None = None) -> Path:
    dest = path or status_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot()
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)
    return dest


def write_proc_detail(path: Path | None = None) -> Path:
    """单独写进程+线程明细，供可视化在旧心跳尚未重载时读取。"""
    dest = path or status_path().with_name("proc_detail.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    procs = collect_processes()
    worker_pid = next(
        (p["pid"] for p in procs if "worker_main" in str(p.get("cmdline") or "")),
        os.getpid(),
    )
    payload = {
        "updated_at": _utc_now(),
        "processes": procs,
        "memory": summarize_memory(procs, worker_pid, collect_cgroup_memory()),
    }
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dest)
    return dest


def _loop(path: Path, interval: float) -> None:
    detail = path.with_name("proc_detail.json")
    while not _stop.wait(interval):
        try:
            write_status(path)
            write_proc_detail(detail)
        except Exception:  # noqa: BLE001
            continue
    try:
        write_status(path)
        write_proc_detail(detail)
    except Exception:  # noqa: BLE001
        return


def start_heartbeat(path: Path | None = None, interval: float = 2.0) -> None:
    dest = path or status_path()
    try:
        write_status(dest)
        write_proc_detail(dest.with_name("proc_detail.json"))
    except Exception:  # noqa: BLE001
        pass
    t = threading.Thread(target=_loop, args=(dest, interval), name="worker-heartbeat", daemon=True)
    t.start()
