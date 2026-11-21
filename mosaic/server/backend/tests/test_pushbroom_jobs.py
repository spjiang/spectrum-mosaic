"""推扫任务：状态流转、旧一轮消息不串、取消、成果路径、下载凭证、菜单补发。"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.models import SystemSetting
from app.routers.pushbroom import download_ticket, read_download_ticket
from app.services import pushbroom as svc
from app.services import pushbroom_mounts as mounts
from app.services.mq import FakeMQPublisher
from app.services.pushbroom_check import FlightCheck
from app.services.rbac import RBAC_KEY, SEEDED_KEY, seed_new_menus


def _job(tmp_path, **kwargs):
    base = dict(
        id=uuid.uuid4(),
        seq=7,
        status="queued",
        run_attempt=2,
        input_dir="/data/input/pushbroom/FigSpec_1",
        output_dir=str(tmp_path / "out" / "FigSpec_1" / "20261119_100000"),
        params={},
        flight={},
        current_stage=None,
        stage_progress=0.0,
        global_percent=0.0,
        message="",
        error_summary=None,
        stage_timings={},
        products=[],
        started_at=None,
        finished_at=None,
        updated_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _db(job):
    db = MagicMock()
    db.get.return_value = job
    return db


def test_progress_moves_queued_job_to_running(tmp_path):
    job = _job(tmp_path)
    out = svc.apply_progress(_db(job), {"job_id": str(job.id), "run_attempt": 2, "stage_id": "P1_trajectory", "global_percent": 12.5})
    assert out is job
    assert job.status == "running"
    assert job.current_stage == "P1_trajectory"
    assert job.global_percent == 12.5
    assert job.started_at is not None


def test_messages_from_an_older_attempt_are_ignored(tmp_path):
    job = _job(tmp_path, status="running")
    db = _db(job)
    assert svc.apply_progress(db, {"job_id": str(job.id), "run_attempt": 1, "global_percent": 90}) is None
    assert svc.apply_event(db, {"job_id": str(job.id), "run_attempt": 1, "event": "succeeded"}) is None
    assert job.status == "running"
    assert job.global_percent == 0.0


def test_finished_job_does_not_go_back_to_running(tmp_path):
    job = _job(tmp_path, status="succeeded")
    db = _db(job)
    assert svc.apply_progress(db, {"job_id": str(job.id), "run_attempt": 2, "global_percent": 50}) is None
    assert svc.apply_event(db, {"job_id": str(job.id), "run_attempt": 2, "event": "started"}) is None
    assert job.status == "succeeded"


def test_succeeded_event_records_products(tmp_path):
    job = _job(tmp_path, status="running")
    svc.apply_event(_db(job), {"job_id": str(job.id), "run_attempt": 2, "event": "succeeded", "products": ["products/mosaic.hdr"]})
    assert job.status == "succeeded"
    assert job.global_percent == 100.0
    assert job.products == ["products/mosaic.hdr"]
    assert job.finished_at is not None


def test_rejected_event_fails_with_reason(tmp_path):
    job = _job(tmp_path, status="running")
    svc.apply_event(_db(job), {"job_id": str(job.id), "run_attempt": 2, "event": "rejected", "error": "授权已过期"})
    assert job.status == "failed"
    assert job.error_summary == "授权已过期"


def test_started_does_not_override_pending_cancel(tmp_path):
    job = _job(tmp_path, status="cancelling")
    svc.apply_event(_db(job), {"job_id": str(job.id), "run_attempt": 2, "event": "started"})
    assert job.status == "cancelling"


def test_bad_job_id_is_dropped(tmp_path):
    assert svc.apply_event(MagicMock(), {"job_id": "nope", "event": "started"}) is None


def test_cancel_queued_job_finishes_at_once_and_writes_control(tmp_path):
    job = _job(tmp_path)
    mq = FakeMQPublisher()
    svc.cancel_job(_db(job), mq, job, worker_online=True)
    assert job.status == "cancelled"
    ctrl = json.loads((tmp_path / "out" / "FigSpec_1" / "20261119_100000" / "log" / "control.json").read_text())
    assert ctrl == {"cancel": True, "run_attempt": 2}
    assert mq.pushbroom_controls[0]["action"] == "cancel"


def test_cancel_running_job_waits_for_worker(tmp_path):
    job = _job(tmp_path, status="running")
    svc.cancel_job(_db(job), FakeMQPublisher(), job, worker_online=True)
    assert job.status == "cancelling"


def test_cancel_running_job_with_worker_gone_finishes_at_once(tmp_path):
    job = _job(tmp_path, status="running")
    svc.cancel_job(_db(job), FakeMQPublisher(), job, worker_online=False)
    assert job.status == "cancelled"


def test_cancel_finished_job_is_refused(tmp_path):
    job = _job(tmp_path, status="succeeded")
    with pytest.raises(HTTPException) as exc:
        svc.cancel_job(_db(job), FakeMQPublisher(), job, worker_online=True)
    assert exc.value.status_code == 409


def test_failed_publish_marks_job_failed(tmp_path):
    job = _job(tmp_path)
    mq = MagicMock()
    mq.publish_pushbroom_job.side_effect = RuntimeError("down")
    with pytest.raises(HTTPException) as exc:
        svc._commit_and_publish(_db(job), mq, job, "admin")
    assert exc.value.status_code == 503
    assert job.status == "failed"
    assert "down" in job.error_summary


def test_job_message_carries_attempt_and_paths(tmp_path):
    job = _job(tmp_path, params={"gsd_m": 0.05})
    msg = svc.job_message(job, "admin")
    assert msg["schema_version"] == 1
    assert msg["run_attempt"] == 2
    assert msg["output_dir"] == job.output_dir
    assert msg["params"] == {"gsd_m": 0.05}


def test_delete_refuses_active_job(tmp_path):
    job = _job(tmp_path, status="running")
    with pytest.raises(HTTPException):
        svc.delete_job(MagicMock(), job, settings=Settings(), delete_files=False)


def test_archive_zip_packs_products_and_report_only(tmp_path):
    import io
    import zipfile

    job = _job(tmp_path, title="FigSpec_1")
    out = tmp_path / "out" / "FigSpec_1" / "20261119_100000"
    (out / "products").mkdir(parents=True)
    (out / "report").mkdir()
    (out / "process").mkdir()
    (out / "products" / "flight_mosaic.hdr").write_bytes(b"ENVI")
    (out / "products" / "flight_preview.png").write_bytes(b"png")
    (out / "report" / "report.md").write_text("ok", encoding="utf-8")
    (out / "process" / "strips.json").write_text("{}", encoding="utf-8")
    blob = b"".join(svc.iter_archive(job))
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.namelist() == [
            "products/flight_mosaic.hdr",
            "products/flight_preview.png",
            "report/report.md",
        ]
        assert archive.read("products/flight_mosaic.hdr") == b"ENVI"
    assert svc.archive_filename(job) == "FigSpec_1_成果.zip"
    (out / "products" / "flight_mosaic.hdr").unlink()
    (out / "products" / "flight_preview.png").unlink()
    (out / "report" / "report.md").unlink()
    with pytest.raises(HTTPException):
        svc.archive_members(job)


def test_preview_png_is_only_the_display_preview(tmp_path):
    job = _job(tmp_path)
    products = tmp_path / "out" / "FigSpec_1" / "20261119_100000" / "products"
    products.mkdir(parents=True)
    png = products / "flight_preview.png"
    png.write_bytes(b"\x89PNG")
    (products / "other.png").write_bytes(b"no")
    assert svc.preview_png(job) == png.resolve()
    png.unlink()
    assert svc.preview_png(job) is None


def test_only_products_and_report_are_downloadable(tmp_path):
    job = _job(tmp_path)
    out = tmp_path / "out" / "FigSpec_1" / "20261119_100000"
    (out / "products").mkdir(parents=True)
    (out / "process").mkdir()
    (out / "products" / "mosaic.hdr").write_text("ENVI")
    (out / "process" / "strips.json").write_text("{}")
    assert [f["path"] for f in svc.list_files(job)] == ["products/mosaic.hdr"]
    assert svc.resolve_file(job, "products/mosaic.hdr").name == "mosaic.hdr"
    for bad in ("process/strips.json", "products/../process/strips.json", "../x", "products"):
        with pytest.raises(HTTPException):
            svc.resolve_file(job, bad)


def test_input_must_stay_under_the_allowed_root():
    settings = Settings(data_roots="/data")
    assert str(svc.assert_input_allowed("/data/input/pushbroom/a", "/data/input/pushbroom", settings)) == "/data/input/pushbroom/a"
    for bad in ("/data/input/other", "/data/input/pushbroom/../other", ""):
        with pytest.raises(HTTPException):
            svc.assert_input_allowed(bad, "/data/input/pushbroom", settings)


def test_non_admin_uses_own_directories():
    user = SimpleNamespace(username="trial1")
    assert svc.roots_for(user, Settings(), admin=False) == ("/data/input/trial1", "/data/output/runs/trial1/pushbroom")
    assert svc.roots_for(user, Settings(), admin=True) == ("/data/input/pushbroom", "/data/output/pushbroom")


def test_download_ticket_round_trip_and_is_not_a_login_token():
    settings = Settings(jwt_secret="k" * 32)
    job_id = uuid.uuid4()
    ticket = download_ticket(settings, user_id=3, job_id=job_id, path="products/a.spe")
    claims = read_download_ticket(settings, ticket)
    assert claims["uid"] == 3 and claims["job"] == str(job_id) and claims["path"] == "products/a.spe"
    assert "sub" not in claims
    with pytest.raises(HTTPException):
        read_download_ticket(settings, ticket + "x")


class _SettingsDb:
    def __init__(self, rows):
        self.rows = {k: SystemSetting(key=k, value=v) for k, v in rows.items()}

    def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    def commit(self):
        pass


def test_seed_grants_pushbroom_once_to_roles_that_can_execute():
    db = _SettingsDb({RBAC_KEY: {"executor": ["execute", "jobs"], "viewer": ["jobs"]}})
    assert seed_new_menus(db) == ["pushbroom_new", "pushbroom_jobs"]
    saved = db.rows[RBAC_KEY].value
    assert saved["executor"] == ["execute", "jobs", "pushbroom_new", "pushbroom_jobs"]
    assert saved["viewer"] == ["jobs"]
    # 管理员之后收回，再启动不会补回来
    db.rows[RBAC_KEY].value = {"executor": ["execute", "jobs"], "viewer": ["jobs"]}
    assert seed_new_menus(db) == []
    assert db.rows[RBAC_KEY].value["executor"] == ["execute", "jobs"]
    assert db.rows[SEEDED_KEY].value == {"keys": ["pushbroom_jobs", "pushbroom_new"]}


def test_seed_without_saved_mapping_only_records_marker():
    db = _SettingsDb({})
    seed_new_menus(db)
    assert RBAC_KEY not in db.rows
    assert SEEDED_KEY in db.rows


def test_logs_are_in_beijing_time(tmp_path):
    job = _job(tmp_path)
    row = SimpleNamespace(created_at=datetime(2026, 11, 21, 2, 3, 4, tzinfo=timezone.utc), level="info", message="已提交")
    db = MagicMock()
    db.scalars.return_value = [row]
    assert svc.format_logs(db, job) == "2026-11-21 10:03:04 [info] 已提交"


def test_queue_positions_count_running_then_queued_by_enqueue_time():
    t = datetime(2026, 11, 21, tzinfo=timezone.utc)
    run = SimpleNamespace(id=uuid.uuid4(), seq=1, status="running", updated_at=t, created_at=t)
    q1 = SimpleNamespace(id=uuid.uuid4(), seq=5, status="queued", updated_at=t + timedelta(minutes=1), created_at=t)
    q2 = SimpleNamespace(id=uuid.uuid4(), seq=3, status="queued", updated_at=t + timedelta(minutes=2), created_at=t)
    assert svc.queue_positions([q2, run, q1]) == {q1.id: 1, q2.id: 2}


def test_flight_items_attach_mount_and_latest_job(tmp_path, monkeypatch):
    good = FlightCheck(input_dir="/data/input/pushbroom/A", name="A", flight={"started_at": "2026-04-19T05:10:43+00:00"})
    older = FlightCheck(input_dir="/data/input/pushbroom/B", name="B", flight={"started_at": "2026-04-14T05:35:44+00:00"})
    bad = FlightCheck(input_dir="/data/input/pushbroom/C", name="C", errors=["缺少文件：figspec.gps"])
    profile = SimpleNamespace(id=uuid.uuid4(), name="0414 安装", effective_from=datetime(2026, 4, 14).date(), along_offset_m=1.78, boresight_px=-49.8)
    latest = _job(tmp_path, status="running", global_percent=40.0, current_stage="P3_align")
    monkeypatch.setattr(mounts, "load_profiles", lambda db: [profile])
    monkeypatch.setattr(mounts, "load_overrides", lambda db: {})
    monkeypatch.setattr(
        svc, "flight_job_stats", lambda db, owner_id: {"/data/input/pushbroom/A": {"count": 2, "latest": latest, "latest_done": None}}
    )
    items = svc.flight_items(MagicMock(), [bad, older, good], owner_id=None)
    assert [i["name"] for i in items] == ["A", "B", "C"]
    a = items[0]
    assert a["mount"]["name"] == "0414 安装" and a["mount"]["source"] == "date"
    assert a["job_count"] == 2 and a["latest_job"]["status"] == "running" and a["latest_job"]["global_percent"] == 40.0
    assert a["preview_job_id"] is None
    assert items[1]["job_count"] == 0 and items[1]["latest_job"] is None
    assert items[2]["ok"] is False


def test_create_job_copies_the_matched_mount(tmp_path, monkeypatch):
    inp = tmp_path / "in" / "FigSpec_X"
    inp.mkdir(parents=True)
    settings = Settings(
        data_roots=str(tmp_path), pushbroom_input_root=str(tmp_path / "in"), pushbroom_output_root=str(tmp_path / "out")
    )
    check = FlightCheck(input_dir=str(inp), name="FigSpec_X", flight={"bands": 10, "started_at": "2026-04-19T05:10:43+00:00"})
    profile = SimpleNamespace(id=uuid.uuid4(), name="0419 安装", effective_from=None, along_offset_m=1.3, boresight_px=-30.0)
    seen = {}

    def fake_resolve(db, name, started_at):
        seen["args"] = (name, started_at)
        return mounts.Resolved("date", profile)

    monkeypatch.setattr(svc, "quick_check", lambda path: check)
    monkeypatch.setattr(svc, "validate_paths", lambda *a, **k: None)
    monkeypatch.setattr(mounts, "resolve_for", fake_resolve)
    user = SimpleNamespace(id=1, username="admin")
    job = svc.create_job(MagicMock(), settings, FakeMQPublisher(), user=user, admin=True, input_dir=str(inp), title="", params={})
    assert seen["args"] == ("FigSpec_X", "2026-04-19T05:10:43+00:00")
    assert job.params["along_offset_m"] == 1.3 and job.params["boresight_px"] == -30.0
    assert job.params["mount_profile"] == {"id": str(profile.id), "name": "0419 安装", "source": "date"}
