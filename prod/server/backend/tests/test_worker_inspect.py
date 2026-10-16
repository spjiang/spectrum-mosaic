import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.config import Settings
from app.services.worker_inspect import heartbeat_holds_job, inspect_worker, read_heartbeat, status_file


def test_status_file_under_data_root(tmp_path: Path):
    settings = Settings(data_roots=str(tmp_path))
    assert status_file(settings) == tmp_path / ".worker" / "status.json"


def test_inspect_alive(tmp_path: Path, monkeypatch):
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "pid": 7,
                "hostname": "worker",
                "listening": True,
                "processes": [{"pid": 7, "cmdline": "python -m ms_mosaic.worker_main"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.worker_inspect.queue_stats", lambda _u: {"consumers": 1, "messages": 0})
    db = MagicMock()
    db.scalars.return_value = [
        SimpleNamespace(
            id="11111111-1111-1111-1111-111111111111",
            status="running",
            current_stage="S2_at",
            global_percent=12,
            message="空三",
            input_dir="/data/in",
            output_dir="/data/out",
        )
    ]
    out = inspect_worker(db, Settings(data_roots=str(tmp_path)))
    assert out["alive"] is True
    assert out["status"] == "ok"
    assert out["worker"]["pid"] == 7
    assert out["active_jobs"][0]["status"] == "running"
    assert not (out["worker"].get("current_job") or {}).get("job_id")
    assert out["active_jobs"][0]["compute_state"] == "stale"


def test_inspect_attaches_orphan_compute(tmp_path: Path, monkeypatch):
    jid = "1b08d979-44a7-4303-91e4-7d910bc83bd1"
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "pid": 7,
                "listening": True,
                "current_job": None,
                "memory": {"job_rss_mb": 22737.7},
                "processes": [
                    {"pid": 7, "cmdline": "python -m ms_mosaic.worker_main"},
                    {
                        "pid": 3786,
                        "ppid": 1,
                        "rss_mb": 1800.0,
                        "cmdline": "python -c from multiprocessing.spawn import spawn_main",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.worker_inspect.queue_stats", lambda _u: {"consumers": 1, "messages": 0})
    started = datetime.now(timezone.utc)
    db = MagicMock()
    db.scalars.return_value = [
        SimpleNamespace(
            id=jid,
            seq=5,
            status="running",
            current_stage="S2_at",
            completed_stage="S4_dsm",
            global_percent=71.4,
            message="",
            input_dir="/data/in",
            output_dir="/data/out",
            created_at=started,
            started_at=started,
            finished_at=None,
        )
    ]
    out = inspect_worker(db, Settings(data_roots=str(tmp_path)))
    current = out["worker"]["current_job"]
    assert current["job_id"] == jid
    assert current["orphan_compute"] is True
    assert out["active_jobs"][0]["seq"] == 5
    assert out["active_jobs"][0]["started_at"]
    assert out["active_jobs"][0]["compute_state"] == "orphan"


def test_inspect_in_worker_pool_is_live(tmp_path: Path, monkeypatch):
    jid = "1b08d979-44a7-4303-91e4-7d910bc83bd1"
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "pid": 7,
                "listening": True,
                "current_job": {"job_id": jid, "pid": 7},
                "memory": {"job_rss_mb": 18000.0},
                "processes": [
                    {"pid": 7, "ppid": 1, "cmdline": "python -m ms_mosaic.worker_main"},
                    {
                        "pid": 3786,
                        "ppid": 7,
                        "rss_mb": 1800.0,
                        "cmdline": "python -c from multiprocessing.spawn import spawn_main",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.worker_inspect.queue_stats", lambda _u: {"consumers": 1, "messages": 0})
    started = datetime.now(timezone.utc)
    db = MagicMock()
    db.scalars.return_value = [
        SimpleNamespace(
            id=jid,
            seq=5,
            status="running",
            current_stage="S5_ortho",
            completed_stage="S4_dsm",
            global_percent=71.4,
            message="",
            input_dir="/data/in",
            output_dir="/data/out",
            created_at=started,
            started_at=started,
            finished_at=None,
        )
    ]
    out = inspect_worker(db, Settings(data_roots=str(tmp_path)))
    current = out["worker"]["current_job"]
    assert current["job_id"] == jid
    assert current["pid"] == 7
    assert not current.get("orphan_compute")
    assert out["active_jobs"][0]["compute_state"] == "live"
    assert heartbeat_holds_job(Settings(data_roots=str(tmp_path)), jid) is True


def test_inspect_does_not_attach_orphan_for_in_worker_children(tmp_path: Path, monkeypatch):
    jid = "1b08d979-44a7-4303-91e4-7d910bc83bd1"
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "pid": 7,
                "listening": True,
                "current_job": None,
                "memory": {"job_rss_mb": 18000.0},
                "processes": [
                    {"pid": 7, "ppid": 1, "cmdline": "python -m ms_mosaic.worker_main"},
                    {
                        "pid": 3786,
                        "ppid": 7,
                        "rss_mb": 1800.0,
                        "cmdline": "python -c from multiprocessing.spawn import spawn_main",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.worker_inspect.queue_stats", lambda _u: {"consumers": 1, "messages": 0})
    db = MagicMock()
    db.scalars.return_value = [
        SimpleNamespace(
            id=jid,
            seq=5,
            status="running",
            current_stage="S5_ortho",
            completed_stage="S4_dsm",
            global_percent=71.4,
            message="",
            input_dir="/data/in",
            output_dir="/data/out",
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=None,
        )
    ]
    out = inspect_worker(db, Settings(data_roots=str(tmp_path)))
    assert not (out["worker"].get("current_job") or {}).get("orphan_compute")
    assert out["active_jobs"][0]["compute_state"] == "stale"


def test_inspect_stale_file(tmp_path: Path, monkeypatch):
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir()
    stale = datetime.now(timezone.utc) - timedelta(seconds=60)
    path.write_text(json.dumps({"updated_at": stale.isoformat(), "pid": 1}), encoding="utf-8")
    monkeypatch.setattr("app.services.worker_inspect.queue_stats", lambda _u: {"consumers": 1, "messages": 0})
    db = MagicMock()
    db.scalars.return_value = []
    out = inspect_worker(db, Settings(data_roots=str(tmp_path)))
    assert out["alive"] is False
    assert out["status"] == "degraded"


def test_read_heartbeat_missing(tmp_path: Path):
    assert read_heartbeat(tmp_path / "nope.json") is None


def test_inspect_merges_proc_detail_tasks(tmp_path: Path, monkeypatch):
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "pid": 7,
                "listening": True,
                "processes": [{"pid": 45, "ppid": 7, "threads": 29, "cmdline": "spawn"}],
            }
        ),
        encoding="utf-8",
    )
    path.with_name("proc_detail.json").write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "processes": [
                    {
                        "pid": 45,
                        "ppid": 7,
                        "threads": 29,
                        "cmdline": "spawn",
                        "tasks": [
                            {"tid": 45, "comm": "python", "state": "R", "main": True},
                            {"tid": 88, "comm": "OpenBLAS", "state": "S", "main": False},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.worker_inspect.queue_stats", lambda _u: {"consumers": 1, "messages": 0})
    db = MagicMock()
    db.scalars.return_value = []
    out = inspect_worker(db, Settings(data_roots=str(tmp_path)))
    tasks = out["worker"]["processes"][0]["tasks"]
    assert len(tasks) == 2
    assert tasks[1]["tid"] == 88


def _write_heartbeat(tmp_path: Path, current_job: dict) -> Settings:
    path = tmp_path / ".worker" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "current_job": current_job,
            }
        ),
        encoding="utf-8",
    )
    return Settings(data_roots=str(tmp_path))


def test_heartbeat_holds_job(tmp_path: Path):
    jid = "1b08d979-44a7-4303-91e4-7d910bc83bd1"
    settings = _write_heartbeat(tmp_path, {"job_id": jid, "pid": 83})
    assert heartbeat_holds_job(settings, jid) is True
    assert heartbeat_holds_job(settings, "00000000-0000-0000-0000-000000000000") is False


def test_heartbeat_holds_job_orphan_compute_is_false(tmp_path: Path):
    jid = "1b08d979-44a7-4303-91e4-7d910bc83bd1"
    settings = _write_heartbeat(
        tmp_path,
        {"job_id": jid, "pid": 83, "orphan_compute": True},
    )
    assert heartbeat_holds_job(settings, jid) is False


def test_heartbeat_holds_job_missing_pid_is_false(tmp_path: Path):
    jid = "1b08d979-44a7-4303-91e4-7d910bc83bd1"
    settings = _write_heartbeat(tmp_path, {"job_id": jid})
    assert heartbeat_holds_job(settings, jid) is False
