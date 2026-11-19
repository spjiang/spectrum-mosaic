from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from synth import SENSOR_PARAMS

from pb_mosaic import job_child, status, worker_main
from pb_mosaic.jobfiles import done_marker, is_done, parse_job
from pb_mosaic.mq_pub import QUEUE_DLQ, QUEUE_EVENTS, QUEUE_PROGRESS


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def publish(self, queue_name: str, payload: dict[str, Any]) -> None:
        self.sent.append((queue_name, {"schema_version": 1, **payload}))

    def events(self) -> list[str]:
        return [p["event"] for q, p in self.sent if q == QUEUE_EVENTS]


def _job_msg(inp: Path, out: Path, attempt: int = 1, params: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "job_id": str(uuid.uuid4()),
        "run_attempt": attempt,
        "input_dir": str(inp),
        "output_dir": str(out),
        "params": params or {},
    }


@pytest.fixture()
def flight_in_roots(roots, synth_flight):
    inp, out = roots
    # 不能用软链：parse_job 会解析到真实路径，落在允许范围外
    dst = inp / synth_flight.root.name
    shutil.copytree(synth_flight.root, dst)
    return dst, out


# ---- 子进程 ----


def test_child_runs_job_and_reports(flight_in_roots) -> None:
    inp, out = flight_in_roots
    job = parse_job(_job_msg(inp, out / "pushbroom" / "j", params={**SENSOR_PARAMS, "bands": [1, 2, 3]}))
    pub = FakePublisher()
    assert job_child.run_job(job, pub) == "succeeded"
    ev = pub.events()
    assert ev[0] == "started" and ev[-1] == "succeeded"
    assert ev.count("stage_done") == 7
    done = [p for q, p in pub.sent if p.get("event") == "succeeded"][0]
    assert done["run_attempt"] == 1 and done["job_id"] == job.job_id
    assert {p["kind"] for p in done["products"]} >= {"cube", "rgb", "preview", "report"}
    prog = [p for q, p in pub.sent if q == QUEUE_PROGRESS]
    assert prog and all(0 <= p["stage_progress"] <= 100 for p in prog)
    assert prog[-1]["global_percent"] == 100.0
    assert json.loads(done_marker(job).read_text())["status"] == "succeeded"


def test_child_cancelled_before_start(flight_in_roots) -> None:
    inp, out = flight_in_roots
    job = parse_job(_job_msg(inp, out / "j"))
    job.log_dir.mkdir(parents=True)
    (job.log_dir / "control.json").write_text(json.dumps({"cancel": True, "run_attempt": 1}))
    pub = FakePublisher()
    assert job_child.run_job(job, pub) == "cancelled"
    assert pub.events() == ["cancelled"]
    assert is_done(job)


def test_child_reports_pipeline_failure(roots) -> None:
    inp, out = roots
    (inp / "broken").mkdir()
    job = parse_job(_job_msg(inp / "broken", out / "j"))
    pub = FakePublisher()
    assert job_child.run_job(job, pub) == "failed"
    failed = [p for _, p in pub.sent if p.get("event") == "failed"][0]
    assert "缺少文件" in failed["error"]


def test_child_main_rejects_bad_payload(tmp_path: Path) -> None:
    p = tmp_path / "payload.json"
    p.write_text("{}")
    assert job_child.main([str(p)]) == job_child.EXIT_BAD_PAYLOAD


# ---- worker 主进程 ----


@pytest.fixture()
def worker(roots, monkeypatch: pytest.MonkeyPatch):
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(worker_main, "publish_once", lambda url, q, body: sent.append((q, body)))
    w = worker_main.Worker("amqp://test")
    w.sent = sent  # type: ignore[attr-defined]
    return w


def _fake_child(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """把子进程换成一段指定行为的 python 脚本；脚本里 JOB 是任务载荷文件路径。"""
    real = subprocess.Popen

    def popen(cmd, **kw):
        return real([sys.executable, "-c", f"import sys,json,time,os,pathlib; JOB=sys.argv[1]\n{script}", cmd[-1]], **kw)

    monkeypatch.setattr(worker_main.subprocess, "Popen", popen)


def _events(w) -> list[dict]:
    return [b for q, b in w.sent if q == QUEUE_EVENTS]


def test_worker_reports_crashed_child(worker, roots, monkeypatch) -> None:
    inp, out = roots
    _fake_child(monkeypatch, "sys.exit(5)")
    raw = _job_msg(inp / "f", out / "j")
    job = parse_job(raw)
    worker.run_job(job, raw)
    ev = _events(worker)
    assert len(ev) == 1 and ev[0]["event"] == "failed" and "exit 5" in ev[0]["error"]
    assert ev[0]["run_attempt"] == 1
    assert is_done(job)
    assert not status.current_job_path().exists()


def test_worker_reports_oom_kill(worker, roots, monkeypatch) -> None:
    inp, out = roots
    _fake_child(monkeypatch, "os.kill(os.getpid(), 9)")
    raw = _job_msg(inp / "f", out / "j")
    worker.run_job(parse_job(raw), raw)
    assert "内存" in _events(worker)[0]["error"]


def test_worker_trusts_child_done_marker(worker, roots, monkeypatch) -> None:
    inp, out = roots
    _fake_child(
        monkeypatch,
        "p=json.load(open(JOB)); d=pathlib.Path(p['output_dir'])/'log'; d.mkdir(parents=True, exist_ok=True); "
        "(d/'attempt-1.done').write_text('{}')",
    )
    raw = _job_msg(inp / "f", out / "j")
    worker.run_job(parse_job(raw), raw)
    assert _events(worker) == []


def test_worker_cancel_terminates_child(worker, roots, monkeypatch) -> None:
    inp, out = roots
    _fake_child(monkeypatch, "time.sleep(60)")
    raw = _job_msg(inp / "f", out / "j")
    job = parse_job(raw)
    (job.output_dir / "products").mkdir(parents=True)
    (job.output_dir / "products" / "x_mosaic.spe.part").write_bytes(b"0" * 100)
    t = threading.Thread(target=worker.run_job, args=(job, raw))
    t.start()
    while worker.current() is None:
        time.sleep(0.05)
    worker.cancel(str(uuid.uuid4()), 1)  # 别的任务：忽略
    worker.cancel(job.job_id, 2)  # 别的尝试：忽略
    assert worker.current() is not None
    t0 = time.monotonic()
    worker.cancel(job.job_id, 1)
    t.join(10)
    assert not t.is_alive() and time.monotonic() - t0 < 5
    assert [e["event"] for e in _events(worker)] == ["cancelled"]
    assert not list((job.output_dir / "products").glob("*.part"))


def test_worker_abort_reports_reason(worker, roots, monkeypatch) -> None:
    inp, out = roots
    _fake_child(monkeypatch, "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)")
    raw = _job_msg(inp / "f", out / "j")
    job = parse_job(raw)
    t = threading.Thread(target=worker.run_job, args=(job, raw))
    t.start()
    while worker.current() is None:
        time.sleep(0.05)
    worker.abort_current("许可复查失败，任务中止：过期")
    t.join(10)
    ev = _events(worker)
    assert len(ev) == 1 and ev[0]["error"] == "许可复查失败，任务中止：过期"


def test_worker_take_and_busy(worker, roots) -> None:
    inp, out = roots
    worker._take(b"not json")
    worker._take(json.dumps({"schema_version": 9}).encode())
    assert [q for q, _ in worker.sent] == [QUEUE_DLQ, QUEUE_DLQ]
    assert not worker.busy()
    raw = _job_msg(inp / "f", out / "j")
    worker._take(json.dumps(raw).encode())
    assert worker.busy() and worker.incoming.qsize() == 1
    # 同一次运行已结束的重投：丢弃
    done = _job_msg(inp / "f", out / "k")
    from pb_mosaic.jobfiles import mark_done

    mark_done(parse_job(done), "succeeded")
    worker._take(json.dumps(done).encode())
    assert worker.incoming.qsize() == 1


def test_worker_fails_unstarted_on_stop(worker, roots) -> None:
    inp, out = roots
    raw = _job_msg(inp / "f", out / "j")
    worker._take(json.dumps(raw).encode())
    worker.stop.set()
    worker.main_loop()
    ev = _events(worker)
    assert ev[0]["event"] == "failed" and "未开始" in ev[0]["error"]
    assert not worker.busy()


def test_worker_recovers_interrupted_job(worker, roots) -> None:
    inp, out = roots
    raw = _job_msg(inp / "f", out / "j")
    job = parse_job(raw)
    (job.output_dir / "products").mkdir(parents=True)
    (job.output_dir / "products" / "a.spe.part").write_bytes(b"x")
    status.set_current_job({**job.as_dict(), "pid": 1})
    worker.recover_interrupted()
    ev = _events(worker)
    assert ev[0]["event"] == "failed" and "重启" in ev[0]["error"]
    assert not status.current_job_path().exists()
    assert not list((job.output_dir / "products").glob("*.part"))
    worker.recover_interrupted()
    assert len(_events(worker)) == 1


def test_main_loop_runs_queued_job(worker, roots, monkeypatch) -> None:
    inp, out = roots
    _fake_child(monkeypatch, "sys.exit(1)")
    raw = _job_msg(inp / "f", out / "j")
    worker._take(json.dumps(raw).encode())
    t = threading.Thread(target=worker.main_loop)
    t.start()
    deadline = time.monotonic() + 10
    while worker.busy() and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.stop.set()
    t.join(5)
    assert not worker.busy()
    assert _events(worker)[0]["event"] == "failed"


def test_status_snapshot(roots) -> None:
    status.set_listening(True)
    status.write_status()
    s = json.loads(status.status_path().read_text())
    assert s["service"] == "pushbroom-worker" and s["listening"] is True and s["queue"] == "pushbroom.jobs"


def test_signal_group_ignores_dead_process() -> None:
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    p.wait()
    worker_main._signal_group(p, signal.SIGKILL)
