"""推扫拼图入口。任务在独立的推扫 worker 上计算，这里只管提交、状态和成果。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.auth import access_block_reason, get_current_user, is_admin, owns_record, require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.models import PushbroomJob, User
from app.services import pushbroom as svc
from app.services import pushbroom_calibration as calibration
from app.services import pushbroom_mounts as mounts
from app.services.audit import record_audit
from app.services.deps import get_mq
from app.services.mq import PB_QUEUE_JOBS, MQPublisher, queue_stats
from app.services.pushbroom_check import find_flights, quick_check

router = APIRouter(prefix="/api/pushbroom", tags=["pushbroom"])


def require_enabled(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.pushbroom_enabled:
        raise HTTPException(404, "推扫拼图未开启")
    return settings


class PushbroomParams(BaseModel):
    # 不填则按航高和相机参数自动估算
    gsd_m: float | None = Field(default=None, ge=0.01, le=2.0)
    # 1 起始的波段序号。不填输出全部波段
    bands: list[int] | None = None
    # 只为拒绝任务里手填安装参数；真正的值由安装配置决定
    along_offset_m: float | None = None
    boresight_px: float | None = None

    @field_validator("bands")
    @classmethod
    def _bands(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("波段列表不能为空，不选表示全部波段")
        if any(b < 1 for b in value):
            raise ValueError("波段序号从 1 开始")
        if len(set(value)) != len(value):
            raise ValueError("波段序号有重复")
        return sorted(value)


class JobCreate(BaseModel):
    input_dir: str
    title: str = Field(default="", max_length=120)
    params: PushbroomParams = Field(default_factory=PushbroomParams)


class CheckBody(BaseModel):
    input_dir: str


class CalibrationRunBody(BaseModel):
    input_dirs: list[str] = Field(min_length=1, max_length=8)


class MountCreateBody(BaseModel):
    run_id: uuid.UUID
    run_token: str
    name: str = Field(min_length=1, max_length=64)
    effective_from: date


class MountUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    effective_from: date | None = None


class FlightMountBody(BaseModel):
    profile_id: uuid.UUID | None = None


DOWNLOAD_TICKET_SECONDS = 300
_TICKET_TYPE = "pushbroom_download"


def download_ticket(settings: Settings, *, user_id: int, job_id: uuid.UUID, path: str) -> str:
    """只能下载这一个文件的短期凭证。不带 sub，不能当登录令牌用。"""
    exp = datetime.now(timezone.utc) + timedelta(seconds=DOWNLOAD_TICKET_SECONDS)
    return jwt.encode(
        {"typ": _TICKET_TYPE, "uid": user_id, "job": str(job_id), "path": path, "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def read_download_ticket(settings: Settings, ticket: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(ticket, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "下载链接已失效，请重新点击下载") from exc
    if claims.get("typ") != _TICKET_TYPE or "sub" in claims:
        raise HTTPException(401, "下载链接无效")
    return claims


def _worker_state(settings: Settings) -> dict[str, Any]:
    stats = queue_stats(settings.rabbitmq_url, PB_QUEUE_JOBS)
    consumers = stats.get("consumers")
    return {
        "worker_online": bool(consumers) if consumers is not None else None,
        "queue_messages": stats.get("messages"),
    }


def _job_out(job: PushbroomJob, owner: str | None = None, queue_position: int | None = None) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "seq": job.seq,
        "title": job.title,
        "created_by": owner,
        "status": job.status,
        "run_attempt": job.run_attempt,
        "input_dir": job.input_dir,
        "output_dir": job.output_dir,
        "params": dict(job.params or {}),
        "flight": dict(job.flight or {}),
        "current_stage": job.current_stage,
        "stage_progress": job.stage_progress,
        "global_percent": job.global_percent,
        "message": job.message,
        "error_summary": job.error_summary,
        "stage_timings": dict(job.stage_timings or {}),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "has_preview": svc.preview_png(job) is not None,
        "queue_position": queue_position,
    }


def _username(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    row = db.get(User, user_id)
    return row.username if row else None


def _owner_name(db: Session, job: PushbroomJob) -> str | None:
    return _username(db, job.created_by)


def _out(db: Session, job: PushbroomJob) -> dict[str, Any]:
    position = svc.queue_positions(svc.active_jobs(db)).get(job.id) if job.status == "queued" else None
    return _job_out(job, _owner_name(db, job), position)


def _owned(db: Session, job_id: uuid.UUID, user: User) -> PushbroomJob:
    job = svc.get_job(db, job_id)
    if not owns_record(user, job.created_by):
        raise HTTPException(404, "推扫任务不存在")
    return job


@router.get("/status")
def status(
    settings: Settings = Depends(get_settings),
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    if not settings.pushbroom_enabled:
        return {"enabled": False, "worker_online": None, "queue_messages": None}
    return {"enabled": True, **_worker_state(settings)}


def _calibration_dirs(user: User, settings: Settings, raw_dirs: list[str]) -> list[str]:
    input_root, _ = svc.roots_for(user, settings, admin=True)
    dirs: list[str] = []
    for raw in raw_dirs:
        inp = svc.assert_input_allowed(raw, input_root, settings)
        check = quick_check(inp)
        if not check.ok:
            raise HTTPException(400, {"message": f"{inp.name} 核对未通过", "check": check.as_dict()})
        dirs.append(str(inp))
    return dirs


@router.post("/calibration/runs")
def start_calibration(
    body: CalibrationRunBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = calibration.start_run(db, settings, user, _calibration_dirs(user, settings, body.input_dirs))
    return calibration.run_out(settings, row, user.username)


@router.get("/calibration/runs")
def list_calibration_runs(
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    _user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    rows = calibration.list_runs(db)
    return {"items": [calibration.run_out(settings, r, _username(db, r.created_by)) for r in rows]}


@router.get("/calibration/runs/{run_id}")
def get_calibration_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    _user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = calibration.get_run(db, run_id)
    return calibration.run_out(settings, row, _username(db, row.created_by))


@router.get("/mounts")
def list_mounts(
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    input_root, _ = svc.roots_for(user, settings, admin=True)
    checks = [quick_check(p) for p in find_flights(input_root)]
    pairs = [(c.name, c.flight.get("started_at")) for c in checks if c.ok]
    profiles = mounts.load_profiles(db)
    counts = mounts.count_matches(profiles, mounts.load_overrides(db), pairs)
    items = [mounts.profile_out(p, counts.get(str(p.id), 0), _username(db, p.created_by)) for p in profiles]
    return {"items": items, "builtin": dict(mounts.BUILTIN)}


@router.post("/mounts")
def create_mount(
    body: MountCreateBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = mounts.create_profile(
        db,
        settings,
        user,
        run_id=body.run_id,
        run_token=body.run_token,
        name=body.name,
        effective_from=body.effective_from,
    )
    return mounts.profile_out(row, 0, user.username)


@router.patch("/mounts/{profile_id}")
def update_mount(
    profile_id: uuid.UUID,
    body: MountUpdateBody,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    row = mounts.update_profile(db, user, profile_id, name=body.name, effective_from=body.effective_from)
    return mounts.profile_out(row, 0, _username(db, row.created_by))


@router.delete("/mounts/{profile_id}")
def delete_mount(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    mounts.delete_profile(db, user, profile_id)
    return {"ok": True}


@router.put("/flights/{flight_name}/mount")
def set_flight_mount(
    flight_name: str,
    body: FlightMountBody,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("admin")),
) -> dict[str, Any]:
    mounts.set_override(db, user, flight_name, body.profile_id)
    return {"ok": True}


@router.get("/flights")
def flights(
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    admin = is_admin(user)
    input_root, output_root = svc.roots_for(user, settings, admin=admin)
    checks = [quick_check(p) for p in find_flights(input_root)]
    items = svc.flight_items(db, checks, owner_id=None if admin else user.id)
    return {"input_root": input_root, "output_root": output_root, "items": items}


@router.post("/check")
def check(
    body: CheckBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    admin = is_admin(user)
    input_root, _ = svc.roots_for(user, settings, admin=admin)
    inp = svc.assert_input_allowed(body.input_dir, input_root, settings)
    return svc.flight_items(db, [quick_check(inp)], owner_id=None if admin else user.id)[0]


@router.post("/jobs")
def create_job(
    body: JobCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    if body.params.along_offset_m is not None or body.params.boresight_px is not None:
        raise HTTPException(400, "安装参数由安装配置决定，不能在任务里填写")
    job = svc.create_job(
        db,
        settings,
        mq,
        user=user,
        admin=is_admin(user),
        input_dir=body.input_dir,
        title=body.title,
        params=body.params.model_dump(exclude_none=True),
    )
    record_audit(db, user.id, "pushbroom.create", {"job_id": str(job.id), "seq": job.seq, "input_dir": job.input_dir})
    return _out(db, job)


@router.get("/jobs")
def list_jobs(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    input_dir: str | None = None,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    owner_id = None if is_admin(user) else user.id
    resolved = str(Path(input_dir).resolve()) if input_dir else None
    rows, total = svc.page_jobs(db, page, page_size, owner_id=owner_id, status=status, input_dir=resolved)
    positions = svc.queue_positions(svc.active_jobs(db))
    names: dict[int, str | None] = {}
    for row in rows:
        if row.created_by is not None and row.created_by not in names:
            names[row.created_by] = _owner_name(db, row)
    items = [
        _job_out(r, names.get(r.created_by) if r.created_by is not None else None, positions.get(r.id)) for r in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    job = _owned(db, job_id, user)
    out = _out(db, job)
    out["calibration_warning"] = svc.calibration_warning(job.output_dir)
    return out


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    job = _owned(db, job_id, user)
    online = _worker_state(settings)["worker_online"]
    job = svc.cancel_job(db, mq, job, worker_online=online is not False)
    record_audit(db, user.id, "pushbroom.cancel", {"job_id": str(job.id), "seq": job.seq})
    return _out(db, job)


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    job = _owned(db, job_id, user)
    job = svc.retry_job(db, mq, job, username=user.username)
    record_audit(db, user.id, "pushbroom.retry", {"job_id": str(job.id), "seq": job.seq, "run_attempt": job.run_attempt})
    return _out(db, job)


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: uuid.UUID,
    delete_files: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    job = _owned(db, job_id, user)
    detail = {"job_id": str(job.id), "seq": job.seq, "output_dir": job.output_dir, "delete_files": delete_files}
    svc.delete_job(db, job, settings=settings, delete_files=delete_files)
    record_audit(db, user.id, "pushbroom.delete", detail)
    return {"ok": True}


@router.get("/jobs/{job_id}/logs", response_class=PlainTextResponse)
def job_logs(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> str:
    return svc.format_logs(db, _owned(db, job_id, user))


@router.get("/jobs/{job_id}/preview.png")
def job_preview(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> FileResponse:
    path = svc.preview_png(_owned(db, job_id, user))
    if path is None:
        raise HTTPException(404, "还没有结果预览")
    return FileResponse(path, media_type="image/png", filename=path.name)


@router.get("/jobs/{job_id}/files")
def job_files(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    return {"items": svc.list_files(_owned(db, job_id, user))}


class LinkBody(BaseModel):
    path: str


@router.post("/jobs/{job_id}/archive-link")
def archive_link(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    job = _owned(db, job_id, user)
    svc.archive_members(job)
    ticket = download_ticket(settings, user_id=user.id, job_id=job.id, path="*")
    return {"url": f"/api/pushbroom/download?ticket={ticket}", "expires_in": DOWNLOAD_TICKET_SECONDS}


@router.post("/jobs/{job_id}/download-link")
def download_link(
    job_id: uuid.UUID,
    body: LinkBody,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict[str, Any]:
    job = _owned(db, job_id, user)
    svc.resolve_file(job, body.path)
    ticket = download_ticket(settings, user_id=user.id, job_id=job.id, path=body.path)
    return {"url": f"/api/pushbroom/download?ticket={ticket}", "expires_in": DOWNLOAD_TICKET_SECONDS}


@router.get("/download")
def download(
    ticket: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(require_enabled),
) -> FileResponse:
    """浏览器直接下载，不经前端内存。几十 GB 的立方体也能断点续传。"""
    claims = read_download_ticket(settings, ticket)
    user = db.get(User, int(claims["uid"]))
    if user is None or access_block_reason(user):
        raise HTTPException(401, "下载链接已失效")
    job = _owned(db, uuid.UUID(claims["job"]), user)
    if claims.get("path") == "*":
        members = svc.archive_members(job)
        filename = svc.archive_filename(job)
        disposition = f"attachment; filename=\"pushbroom-{job.seq}.zip\"; filename*=UTF-8''{quote(filename)}"
        return StreamingResponse(
            svc.iter_archive_members(members),
            media_type="application/zip",
            headers={"Content-Disposition": disposition},
        )
    path = svc.resolve_file(job, claims["path"])
    return FileResponse(path, filename=path.name)
