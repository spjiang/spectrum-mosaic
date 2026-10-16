from pathlib import Path
import os

from ms_mosaic.worker_status import collect_processes, snapshot, write_status


def test_snapshot_has_pid_and_process():
    snap = snapshot()
    assert snap["pid"] > 0
    assert snap["queue"] == "mosaic.jobs"
    assert isinstance(snap["processes"], list)
    assert any(r["pid"] == snap["pid"] for r in snap["processes"])
    assert snap["thread_count"] >= 1


def test_write_status(tmp_path: Path):
    dest = tmp_path / "status.json"
    write_status(dest)
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    assert "mosaic.jobs" in text


def test_collect_processes_includes_self():
    rows = collect_processes()
    assert rows
    assert any(r["pid"] > 0 for r in rows)


def test_collect_processes_includes_child():
    import os
    import subprocess
    import time

    if not Path("/proc").is_dir():
        return
    child = subprocess.Popen(["sleep", "8"])
    try:
        time.sleep(0.2)
        rows = collect_processes()
        pids = {r["pid"] for r in rows}
        assert child.pid in pids
        rec = next(r for r in rows if r["pid"] == child.pid)
        assert rec.get("ppid") == os.getpid()
        parent = next(r for r in rows if r["pid"] == os.getpid())
        assert parent.get("child_count", 0) >= 1
        assert rec.get("depth", 0) >= 1
    finally:
        child.terminate()
        child.wait(timeout=3)


def test_collect_processes_lists_linux_tasks():
    import os

    if not Path("/proc").is_dir():
        return
    rows = collect_processes()
    mine = next(r for r in rows if r["pid"] == os.getpid())
    assert mine["tasks"]
    assert any(t["tid"] == os.getpid() and t["main"] for t in mine["tasks"])


def test_summarize_memory_idle_job_is_zero():
    from ms_mosaic.worker_status import summarize_memory

    mem = summarize_memory(
        [
            {"pid": 7, "ppid": 1, "rss_mb": 58.2, "cmdline": "python -m ms_mosaic.worker_main"},
            {
                "pid": 583,
                "ppid": 7,
                "rss_mb": 5.6,
                "cmdline": "python -c from multiprocessing.resource_tracker import main;main(11)",
            },
        ],
        root_pid=7,
    )
    assert mem["worker_rss_mb"] == 58.2
    assert mem["helper_rss_mb"] == 5.6
    assert mem["job_rss_mb"] == 0.0
    assert mem["tree_rss_mb"] == 63.8


def test_summarize_memory_job_child_counted():
    from ms_mosaic.worker_status import summarize_memory

    mem = summarize_memory(
        [
            {"pid": 7, "ppid": 1, "rss_mb": 58.2, "cmdline": "python -m ms_mosaic.worker_main"},
            {"pid": 900, "ppid": 7, "rss_mb": 812.4, "cmdline": "python -c from multiprocessing.spawn import spawn_main"},
        ],
        root_pid=7,
    )
    assert mem["job_rss_mb"] == 812.4
    assert mem["worker_rss_mb"] == 58.2


def test_snapshot_keeps_last_job_when_compute_rss(monkeypatch):
    from ms_mosaic import worker_status

    worker_status.set_current_job({"job_id": "abc", "pid": 99})
    worker_status.set_current_job(None)
    monkeypatch.setattr(
        worker_status,
        "collect_processes",
        lambda _pid=None: [
            {"pid": os.getpid(), "cmdline": "python -m ms_mosaic.worker_main", "rss_mb": 10},
            {"pid": 90, "ppid": 1, "cmdline": "spawn_main", "rss_mb": 800},
        ],
    )
    try:
        snap = worker_status.snapshot()
        assert snap["current_job"]["job_id"] == "abc"
        assert snap["current_job"]["orphan_compute"] is True
    finally:
        worker_status.set_current_job(None, remember=False)


def test_snapshot_in_worker_job_is_not_orphan(monkeypatch):
    from ms_mosaic import worker_status

    pid = os.getpid()
    worker_status.set_current_job({"job_id": "abc", "pid": pid, "orphan_compute": True})
    monkeypatch.setattr(
        worker_status,
        "collect_processes",
        lambda _pid=None: [
            {"pid": pid, "ppid": 1, "cmdline": "python -m ms_mosaic.worker_main", "rss_mb": 10},
            {"pid": 90, "ppid": pid, "cmdline": "spawn_main", "rss_mb": 800},
        ],
    )
    try:
        snap = worker_status.snapshot()
        assert snap["current_job"]["job_id"] == "abc"
        assert snap["current_job"]["pid"] == pid
        assert not snap["current_job"].get("orphan_compute")
    finally:
        worker_status.set_current_job(None, remember=False)


def test_snapshot_memory_always_present():
    snap = snapshot()
    mem = snap["memory"]
    assert mem["job_rss_mb"] >= 0
    assert mem["worker_rss_mb"] >= 0
    assert mem["tree_rss_mb"] >= 0
    assert "container_used_mb" in mem
    assert "container_limit_mb" in mem
