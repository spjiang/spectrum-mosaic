from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse, Response as RawResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.auth import is_admin, owns_record, require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.models import JobRun, User
from app.schemas import (
    AccuracyConfigIn,
    AccuracyGpsIn,
    AccuracyPickIn,
    AccuracyState,
    EvaluateIn,
    FileTicketIn,
    JobContinue,
    JobCreate,
    JobListOut,
    JobOut,
    JobsClearedOut,
    SeamBranchIn,
    SeamPointsIn,
    SeamPreferIn,
    SeamTaskCreate,
)
from app.services import jobs as jobsvc
from app.services.seam_map import (
    build_seam_map,
    images_covering,
    images_covering_polygon,
    list_product_versions,
    preview_png,
    rgb_ortho_path,
    rgb_hd_jpeg,
    rgb_preview_png,
    rgb_thumb_jpeg,
    source_image_jpeg,
)
from app.services.stage_steps import persist_stage_steps
from app.services.audit import record_audit
from app.services.deps import get_mq
from app.services.mq import MQPublisher

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _owned_job(db: Session, job_id: uuid.UUID, user: User) -> JobRun:
    job = db.get(JobRun, job_id)
    if job is None or not owns_record(user, job.created_by):
        raise HTTPException(404, "任务不存在")
    return job


@router.post("", response_model=JobOut)
def create_job(
    body: JobCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    job = jobsvc.create_job(
        db,
        settings=settings,
        mq=mq,
        user_id=user.id,
        profile_id=body.profile_id,
        params=body.params,
        input_dir=body.input_dir,
        output_dir=body.output_dir,
        title=body.title,
    )
    record_audit(
        db,
        user.id,
        "job.create",
        {"job_id": str(job.id), "seq": job.seq, "input_dir": job.input_dir, "output_dir": job.output_dir},
    )
    return job


_JOB_LIST_COLS = (
    JobRun.id,
    JobRun.seq,
    JobRun.title,
    JobRun.status,
    JobRun.run_attempt,
    JobRun.profile_id,
    JobRun.profile_version,
    JobRun.input_dir,
    JobRun.output_dir,
    JobRun.cache_dir,
    JobRun.log_dir,
    JobRun.process_dir,
    JobRun.products_dir,
    JobRun.report_pdf_path,
    JobRun.current_stage,
    JobRun.completed_stage,
    JobRun.global_percent,
    JobRun.stage_progress,
    JobRun.message,
    JobRun.eta_seconds,
    JobRun.error_summary,
    JobRun.stage_timings,
    JobRun.stage_steps,
    JobRun.n_shots,
    JobRun.memory_gb,
    JobRun.cpus,
    JobRun.gpu,
    JobRun.gpu_memory_gb,
    JobRun.native_gsd_m,
    JobRun.ortho_gsd_m,
    JobRun.created_at,
    JobRun.updated_at,
    JobRun.started_at,
    JobRun.finished_at,
)


@router.get("", response_model=list[JobListOut])
def list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> list[JobRun]:
    stmt = (
        select(JobRun)
        .options(load_only(*_JOB_LIST_COLS))
        .order_by(JobRun.created_at.desc())
        .limit(200)
    )
    if not is_admin(user):
        stmt = stmt.where(JobRun.created_by == user.id)
    rows = list(db.scalars(stmt))
    for row in rows:
        row.has_rgb = rgb_ortho_path(row) is not None
    return rows


@router.get("/seam-tasks", response_model=list[JobOut])
def seam_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> list[JobRun]:
    rows = jobsvc.list_seam_tasks(db)
    if is_admin(user):
        return rows
    return [row for row in rows if row.created_by == user.id]


@router.post("/seam-tasks", response_model=JobOut)
def create_seam_task(
    body: SeamTaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    _owned_job(db, body.source_job_id, user)
    job = jobsvc.create_seam_task(db, user_id=user.id, source_id=body.source_job_id)
    record_audit(db, user.id, "job.seam_task", {"job_id": str(job.id), "source_job_id": str(body.source_job_id)})
    return job


@router.delete("", response_model=JobsClearedOut)
def clear_jobs(
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobsClearedOut:
    owner_id = None if is_admin(user) else user.id
    deleted = jobsvc.delete_all_jobs(db, mq, owner_id=owner_id)
    record_audit(db, user.id, "job.clear", {"deleted": deleted})
    return JobsClearedOut(deleted=deleted)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> JobRun:
    job = _owned_job(db, job_id, user)
    if job.status in {"queued", "running", "paused"}:
        eta = jobsvc.estimate_eta_seconds(db, job, float(job.global_percent or 0))
        if eta is not None:
            job.eta_seconds = eta
    return job


@router.delete("/{job_id}")
def delete_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> Response:
    _owned_job(db, job_id, user)
    jobsvc.delete_job(db, mq, job_id)
    record_audit(db, user.id, "job.delete", {"job_id": str(job_id)})
    return Response(status_code=204)


@router.get("/{job_id}/seam-versions")
def seam_versions(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = _owned_job(db, job_id, user)
    return list_product_versions(job)


@router.get("/{job_id}/seam-map")
def seam_map(
    job_id: uuid.UUID,
    version: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = _owned_job(db, job_id, user)
    payload = build_seam_map(job, version)
    payload.pop("stamp", None)
    return payload


@router.get("/{job_id}/rgb-thumb.jpg")
def rgb_thumb(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = _owned_job(db, job_id, user)
    return RawResponse(
        content=rgb_thumb_jpeg(job),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{job_id}/rgb-hd.jpg")
def rgb_hd(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = _owned_job(db, job_id, user)
    return RawResponse(
        content=rgb_hd_jpeg(job),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{job_id}/rgb-preview.png")
def rgb_preview(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = _owned_job(db, job_id, user)
    return RawResponse(content=rgb_preview_png(job), media_type="image/png")


@router.get("/{job_id}/seam-preview.png")
def seam_preview(
    job_id: uuid.UUID,
    version: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = _owned_job(db, job_id, user)
    return RawResponse(content=preview_png(job, version), media_type="image/png")


@router.get("/{job_id}/seam-candidates")
def seam_candidates(
    job_id: uuid.UUID,
    x: float,
    y: float,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = _owned_job(db, job_id, user)
    return {"images": images_covering(job, x, y)}


@router.post("/{job_id}/seam-candidates")
def seam_candidates_many(
    job_id: uuid.UUID,
    body: SeamPointsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = _owned_job(db, job_id, user)
    return {"images": images_covering_polygon(job, body.points[:80])}


@router.get("/{job_id}/seam-image/{image_id}")
def seam_image(
    job_id: uuid.UUID,
    image_id: int,
    max_side: int = 480,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = _owned_job(db, job_id, user)
    return RawResponse(content=source_image_jpeg(job, image_id, max_side=max_side), media_type="image/jpeg")


_ACCURACY_ROLES = ("executor", "viewer", "admin", "configurator")


def _accuracy_job(job_id: uuid.UUID, db: Session, user: User) -> JobRun:
    return _owned_job(db, job_id, user)


@router.get("/{job_id}/accuracy", response_model=AccuracyState)
def accuracy_state(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    from app.services.check_accuracy import build_state

    return build_state(_accuracy_job(job_id, db, user))


@router.get("/{job_id}/accuracy/template.csv")
def accuracy_template(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> PlainTextResponse:
    from app.services.check_accuracy import template_csv

    _accuracy_job(job_id, db, user)
    return PlainTextResponse(
        template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=check_points.csv"},
    )


@router.post("/{job_id}/accuracy/points", response_model=AccuracyState)
def accuracy_add_point(
    job_id: uuid.UUID,
    body: AccuracyPickIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    from app.services.check_accuracy import add_point, move_point

    job = _accuracy_job(job_id, db, user)
    if body.name:
        return move_point(job, body.name, body.col, body.row)
    return add_point(job, body.col, body.row)


@router.delete("/{job_id}/accuracy/points/{name}", response_model=AccuracyState)
def accuracy_delete_point(
    job_id: uuid.UUID,
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    from app.services.check_accuracy import delete_point

    return delete_point(_accuracy_job(job_id, db, user), name)


@router.put("/{job_id}/accuracy/points/{name}/gps", response_model=AccuracyState)
def accuracy_gps(
    job_id: uuid.UUID,
    name: str,
    body: AccuracyGpsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    from app.services.check_accuracy import set_gps

    return set_gps(_accuracy_job(job_id, db, user), name, body.x, body.y, body.z, body.crs)


@router.post("/{job_id}/accuracy/evaluate", response_model=AccuracyState)
def accuracy_evaluate(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    from app.services.check_accuracy import run_evaluation

    return run_evaluation(_accuracy_job(job_id, db, user))


@router.put("/{job_id}/accuracy/config", response_model=AccuracyState)
def accuracy_config(
    job_id: uuid.UUID,
    body: AccuracyConfigIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    from app.services.check_accuracy import update_config

    return update_config(
        _accuracy_job(job_id, db, user),
        body.min_count,
        body.max_count,
        body.max_mean_px,
        body.reference_band,
    )


@router.get("/{job_id}/accuracy/window")
def accuracy_window(
    job_id: uuid.UUID,
    band: str = "",
    cx: float | None = None,
    cy: float | None = None,
    size: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> RawResponse:
    from app.services.check_accuracy import window_png

    png, meta = window_png(_accuracy_job(job_id, db, user), band, cx, cy, size)
    headers = {
        "Cache-Control": "no-store",
        "X-Origin-Col": str(meta["origin_col"]),
        "X-Origin-Row": str(meta["origin_row"]),
        "X-Win-Width": str(meta["width"]),
        "X-Win-Height": str(meta["height"]),
        "X-Full-Width": str(meta["full_width"]),
        "X-Full-Height": str(meta["full_height"]),
    }
    return RawResponse(content=png, media_type="image/png", headers=headers)


@router.get("/{job_id}/accuracy/overview")
def accuracy_overview(
    job_id: uuid.UUID,
    band: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> RawResponse:
    from app.services.check_accuracy import overview_png

    png, meta = overview_png(_accuracy_job(job_id, db, user), band)
    headers = {
        "Cache-Control": "no-store",
        "X-Win-Width": str(meta["width"]),
        "X-Win-Height": str(meta["height"]),
        "X-Full-Width": str(meta["full_width"]),
        "X-Full-Height": str(meta["full_height"]),
    }
    return RawResponse(content=png, media_type="image/png", headers=headers)


@router.get("/{job_id}/accuracy/browse")
def accuracy_browse(
    job_id: uuid.UUID,
    band: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> RawResponse:
    from app.services.check_accuracy import browse_jpeg

    data, meta = browse_jpeg(_accuracy_job(job_id, db, user), band)
    headers = {
        "Cache-Control": "no-store",
        "X-Win-Width": str(meta["width"]),
        "X-Win-Height": str(meta["height"]),
        "X-Full-Width": str(meta["full_width"]),
        "X-Full-Height": str(meta["full_height"]),
    }
    return RawResponse(content=data, media_type="image/jpeg", headers=headers)


@router.post("/{job_id}/evaluate", response_model=AccuracyState)
def evaluate_job(
    job_id: uuid.UUID,
    body: EvaluateIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_ACCURACY_ROLES)),
) -> dict:
    job = _accuracy_job(job_id, db, user)
    n = 20 if body is None else int(body.n)
    return jobsvc.evaluate_job(job, n)


@router.post("/{job_id}/seam-branch", response_model=JobOut)
def branch_seam_copy(
    job_id: uuid.UUID,
    body: SeamBranchIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    _owned_job(db, job_id, user)
    job = jobsvc.branch_seam_copy(db, job_id, body.version)
    record_audit(db, user.id, "job.seam_branch", {"job_id": str(job.id), "seq": job.seq, "version": body.version})
    return job


@router.post("/{job_id}/seam-cancel", response_model=JobOut)
def cancel_reseam_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    _owned_job(db, job_id, user)
    job = jobsvc.cancel_reseam_job(db, mq, job_id)
    record_audit(db, user.id, "job.seam_cancel", {"job_id": str(job.id), "seq": job.seq})
    return job


@router.post("/{job_id}/seam", response_model=JobOut)
def reseam_job(
    job_id: uuid.UUID,
    body: SeamPreferIn,
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    _owned_job(db, job_id, user)
    job = jobsvc.reseam_job(
        db,
        mq,
        job_id,
        body.prefer_ids,
        regions=[item.model_dump() for item in body.regions],
        clear_edits=body.clear_edits,
    )
    record_audit(db, user.id, "job.reseam", {"job_id": str(job.id), "seq": job.seq, "prefer_ids": body.prefer_ids})
    return job


@router.post("/{job_id}/{action}", response_model=JobOut)
def job_action(
    job_id: uuid.UUID,
    action: str,
    body: JobContinue | None = None,
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    _owned_job(db, job_id, user)
    stop = body.stop_after_stage if body else None
    job = jobsvc.control_job(db, mq, job_id, action, stop_after=stop, settings=settings)
    record_audit(db, user.id, f"job.{action}", {"job_id": str(job.id), "seq": job.seq})
    return job


@router.get("/{job_id}/stage-steps")
def job_stage_steps(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = _owned_job(db, job_id, user)
    payload = persist_stage_steps(job)
    db.commit()
    return payload


@router.get("/{job_id}/logs")
def job_logs(
    job_id: uuid.UUID,
    tail: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> PlainTextResponse:
    job = _owned_job(db, job_id, user)
    return PlainTextResponse(jobsvc.format_job_logs(db, job, tail=tail))


@router.get("/{job_id}/report.pdf")
def job_report(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> FileResponse:
    job = _owned_job(db, job_id, user)
    path = jobsvc.locate_report_pdf(job)
    if path is None:
        raise HTTPException(404, "质量报告尚未生成")
    return FileResponse(path, filename="质量报告.pdf", media_type="application/pdf")


_FILE_ROLES = ("executor", "viewer", "admin", "configurator")


def _file_job(job_id: uuid.UUID, db: Session, user: User) -> JobRun:
    return _owned_job(db, job_id, user)


@router.get("/{job_id}/files")
def job_files(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_FILE_ROLES)),
) -> dict:
    from app.services.job_files import catalog

    return catalog(_file_job(job_id, db, user))


@router.post("/{job_id}/files/ticket")
def job_file_ticket(
    job_id: uuid.UUID,
    body: FileTicketIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_FILE_ROLES)),
) -> dict:
    from app.services.job_files import issue_ticket

    _file_job(job_id, db, user)
    return {"ticket": issue_ticket(str(job_id), body.kind, body.name)}


@router.get("/{job_id}/files/download")
def job_file_download(
    job_id: uuid.UUID,
    ticket: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    from starlette.background import BackgroundTask

    from app.services.job_files import build_archive, drop_file, original_file, read_ticket

    row = read_ticket(ticket, str(job_id))
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    kind = row["kind"]
    if kind == "file":
        path = original_file(job, row["name"])
        return FileResponse(path, filename=path.name, media_type="image/tiff")
    path, filename = build_archive(job, kind)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(drop_file, path),
    )


@router.get("/{job_id}/quality")
def job_quality(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_FILE_ROLES)),
) -> dict:
    from app.services.job_files import quality_view

    return quality_view(_file_job(job_id, db, user))


@router.get("/{job_id}/quality/figures/{name}")
def job_quality_figure(
    job_id: uuid.UUID,
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(*_FILE_ROLES)),
) -> FileResponse:
    from app.services.job_files import figure_file

    path = figure_file(_file_job(job_id, db, user), name)
    return FileResponse(path, media_type="image/png")
