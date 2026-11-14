import subprocess
import time

from ms_mosaic.job_proc import terminate_job_workers, terminate_process_tree


def test_terminate_process_tree_stops_child():
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert proc.poll() is None
        terminate_process_tree(proc.pid, timeout=3)
        deadline = time.time() + 3
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_terminate_job_workers_skips_worker(monkeypatch):
    from ms_mosaic import job_proc

    monkeypatch.setattr(job_proc, "descendants", lambda pid: [pid, 10, 11, 12])
    monkeypatch.setattr(
        job_proc,
        "_cmdline",
        lambda pid: {
            7: "python -m ms_mosaic.worker_main",
            10: "python -c from multiprocessing.spawn import spawn_main",
            11: "python -m ms_mosaic.job_child --ms-job-abc /tmp/p.json",
            12: "resource_tracker",
        }.get(pid, ""),
    )
    sent: list[list[int]] = []
    monkeypatch.setattr(job_proc, "_signal_all", lambda pids, _sig: sent.append(list(pids)))
    monkeypatch.setattr(job_proc, "_alive", lambda _pid: False)
    monkeypatch.setattr(job_proc, "_orphan_spawn_pids", lambda _pid: [])
    leftover = terminate_job_workers(7)
    assert leftover == []
    assert sent[0] == [10, 11]
    assert any("ms-job-" in job_proc._cmdline(pid) for pid in sent[0])
