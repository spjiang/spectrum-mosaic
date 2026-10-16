from pathlib import Path
from unittest.mock import MagicMock

from ms_mosaic.job_child import handle_job
from ms_mosaic.worker_main import _claim_job, _release_job, _run_payload, delivery_action, job_child_command


def test_delivery_action_ignores_redelivery_of_running_job():
    assert delivery_action("job-1", True, "job-1") == "ignore"
    assert delivery_action("job-1", True, "job-2") == "busy"
    assert delivery_action(None, False, "job-1") == "run"
    assert delivery_action("job-1", False, "job-1") == "run"


def test_claim_job_blocks_second_job():
    assert _claim_job("job-1") == "run"
    try:
        assert _claim_job("job-1") == "ignore"
        assert _claim_job("job-2") == "busy"
    finally:
        _release_job("job-1")
    assert _claim_job("job-2") == "run"
    _release_job("job-2")


def test_worker_main_does_not_load_algorithm_stack():
    import subprocess
    import sys

    script = (
        "import ms_mosaic.worker_main, sys; "
        "assert 'ms_mosaic.job_child' not in sys.modules; "
        "assert 'ms_mosaic.stage_runner' not in sys.modules"
    )
    subprocess.check_call([sys.executable, "-c", script])


def test_job_child_command_marks_process():
    cmd = job_child_command("job-live", Path("/tmp/payload.json"))
    assert cmd[1:3] == ["-m", "ms_mosaic.job_child"]
    assert "--ms-job-job-live" in cmd
    assert cmd[-1] == "/tmp/payload.json"


def test_job_child_main_runs_handle_job(tmp_path: Path, monkeypatch):
    from ms_mosaic import job_child

    payload = {"job_id": "job-1", "params_snapshot": {"output_dir": str(tmp_path)}}
    path = tmp_path / "job_payload.json"
    path.write_text('{"job_id": "job-1", "params_snapshot": {}}', encoding="utf-8")
    seen: dict = {}

    def fake_handle(channel, body, *, rabbitmq_url, connect=None):
        seen["channel"] = channel
        seen["body"] = body
        seen["url"] = rabbitmq_url

    monkeypatch.setattr(job_child, "handle_job", fake_handle)
    assert job_child.main(["--ms-job-job-1", str(path)], rabbitmq_url="amqp://x") == 0
    assert seen["channel"] is None
    assert seen["body"]["job_id"] == "job-1"
    assert seen["url"] == "amqp://x"


def test_run_payload_uses_job_child_pid(tmp_path: Path, monkeypatch):
    from ms_mosaic import worker_status
    from ms_mosaic.worker_main import _job_running

    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    seen: dict = {}

    class FakeProc:
        pid = 4242

        def wait(self):
            snap = worker_status.snapshot()
            seen["job"] = dict(snap["current_job"] or {})
            seen["running"] = _job_running()
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr("ms_mosaic.worker_main._spawn_job", lambda _url, _payload: FakeProc())
    monkeypatch.setattr("ms_mosaic.worker_main.terminate_job_workers", lambda _pid: [])
    _run_payload(
        "amqp://mosaic:mosaic_secret@127.0.0.1:5672/",
        {
            "job_id": "job-live",
            "params_snapshot": {"input_dir": str(inp), "output_dir": str(out)},
        },
    )
    assert seen["running"] is True
    assert seen["job"]["job_id"] == "job-live"
    assert seen["job"]["pid"] == 4242
    assert not seen["job"].get("orphan_compute")
    assert _job_running() is False
    assert worker_status.snapshot().get("current_job") is None


def test_child_sigkill_is_reported_as_failed(tmp_path: Path, monkeypatch):
    from ms_mosaic import worker_main

    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    published: list[bytes] = []

    class FakeProc:
        pid = 77

        def wait(self):
            return -9

        def poll(self):
            return -9

    class FakeConn:
        def channel(self):
            return self

        def basic_publish(self, **kwargs):
            published.append(kwargs["body"])

        def close(self):
            return None

    monkeypatch.setattr("ms_mosaic.worker_main._spawn_job", lambda _url, _payload: FakeProc())
    monkeypatch.setattr("ms_mosaic.worker_main.terminate_job_workers", lambda _pid: [])
    monkeypatch.setattr("ms_mosaic.worker_main.connect_amqp", lambda _url: FakeConn())
    worker_main._run_payload(
        "amqp://example",
        {"job_id": "job-killed", "params_snapshot": {"input_dir": str(inp), "output_dir": str(out)}},
    )
    body = published[0].decode("utf-8")
    assert "job-killed" in body
    assert "failed" in body
    assert "exit -9" in body


def test_kill_current_job_stops_job_child(monkeypatch):
    from ms_mosaic import worker_main

    killed: list[int] = []
    monkeypatch.setattr(worker_main, "terminate_process_tree", lambda pid, timeout=5.0: killed.append(pid))
    monkeypatch.setattr(worker_main, "terminate_job_workers", lambda _pid: [])
    worker_main._job_id = "job-x"
    worker_main._job_active = True
    worker_main._job_child_pid = 555
    try:
        assert worker_main.kill_current_job() == "job-x"
        assert killed == [555]
    finally:
        worker_main._job_id = None
        worker_main._job_active = False
        worker_main._job_child_pid = None


def test_handle_job_runs_stages_and_reports_success(tmp_path: Path, monkeypatch):
    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    seen: list[tuple] = []

    def fake_run_stages(input_dir, output_dir, params=None, reporter=None, control=None):
        seen.append((input_dir, output_dir, params))
        reporter.event("succeeded", n_shots=2)
        return {"status": "succeeded", "n_shots": 2}

    monkeypatch.setattr("ms_mosaic.job_child.run_stages", fake_run_stages)
    channel = MagicMock()
    ctrl_conn = MagicMock()
    handle_job(
        channel,
        {
            "job_id": "job-1",
            "params_snapshot": {"input_dir": str(inp), "output_dir": str(out), "bands": ["Color"]},
        },
        rabbitmq_url="amqp://mosaic:mosaic_secret@127.0.0.1:5672/",
        connect=lambda _url: ctrl_conn,
    )
    assert seen[0][0] == inp
    assert seen[0][1] == out
    assert seen[0][2]["bands"] == ["Color"]
    published = b"".join(call.kwargs.get("body") or b"" for call in channel.basic_publish.call_args_list)
    assert b"succeeded" in published
