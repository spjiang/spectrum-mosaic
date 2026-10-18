from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse, Response as RawResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.auth import require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.models import JobRun, User
from app.schemas import (
    EvaluateIn,
    EvaluateOut,
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
    rgb_preview_png,
    source_image_jpeg,
)
from app.services.stage_steps import persist_stage_steps
from app.services.audit import record_audit
from app.services.deps import get_mq
from app.services.mq import MQPublisher

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


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
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> list[JobRun]:
    rows = list(
        db.scalars(
            select(JobRun)
            .options(load_only(*_JOB_LIST_COLS))
            .order_by(JobRun.created_at.desc())
            .limit(200)
        )
    )
    for row in rows:
        row.has_rgb = rgb_ortho_path(row) is not None
    return rows


@router.get("/seam-tasks", response_model=list[JobOut])
def seam_tasks(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> list[JobRun]:
    return jobsvc.list_seam_tasks(db)


@router.post("/seam-tasks", response_model=JobOut)
def create_seam_task(
    body: SeamTaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
    job = jobsvc.create_seam_task(db, user_id=user.id, source_id=body.source_job_id)
    record_audit(db, user.id, "job.seam_task", {"job_id": str(job.id), "source_job_id": str(body.source_job_id)})
    return job


@router.delete("", response_model=JobsClearedOut)
def clear_jobs(
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobsClearedOut:
    deleted = jobsvc.delete_all_jobs(db, mq)
    record_audit(db, user.id, "job.clear", {"deleted": deleted})
    return JobsClearedOut(deleted=deleted)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> JobRun:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
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
    jobsvc.delete_job(db, mq, job_id)
    record_audit(db, user.id, "job.delete", {"job_id": str(job_id)})
    return Response(status_code=204)


@router.get("/{job_id}/seam-versions")
def seam_versions(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return list_product_versions(job)


@router.get("/{job_id}/seam-map")
def seam_map(
    job_id: uuid.UUID,
    version: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    payload = build_seam_map(job, version)
    payload.pop("stamp", None)
    return payload


@router.get("/{job_id}/rgb-preview.png")
def rgb_preview(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return RawResponse(content=rgb_preview_png(job), media_type="image/png")


@router.get("/{job_id}/seam-preview.png")
def seam_preview(
    job_id: uuid.UUID,
    version: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return RawResponse(content=preview_png(job, version), media_type="image/png")


@router.get("/{job_id}/seam-candidates")
def seam_candidates(
    job_id: uuid.UUID,
    x: float,
    y: float,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {"images": images_covering(job, x, y)}


@router.post("/{job_id}/seam-candidates")
def seam_candidates_many(
    job_id: uuid.UUID,
    body: SeamPointsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {"images": images_covering_polygon(job, body.points[:80])}


@router.get("/{job_id}/seam-image/{image_id}")
def seam_image(
    job_id: uuid.UUID,
    image_id: int,
    max_side: int = 480,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> RawResponse:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return RawResponse(content=source_image_jpeg(job, image_id, max_side=max_side), media_type="image/jpeg")


@router.post("/{job_id}/evaluate", response_model=EvaluateOut)
def evaluate_job(
    job_id: uuid.UUID,
    body: EvaluateIn | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    n = 20 if body is None else int(body.n)
    return jobsvc.evaluate_job(job, n)


@router.post("/{job_id}/seam-branch", response_model=JobOut)
def branch_seam_copy(
    job_id: uuid.UUID,
    body: SeamBranchIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "admin")),
) -> JobRun:
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
    stop = body.stop_after_stage if body else None
    job = jobsvc.control_job(db, mq, job_id, action, stop_after=stop, settings=settings)
    record_audit(db, user.id, f"job.{action}", {"job_id": str(job.id), "seq": job.seq})
    return job


@router.get("/{job_id}/stage-steps")
def job_stage_steps(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    payload = persist_stage_steps(job)
    db.commit()
    return payload


@router.get("/{job_id}/logs")
def job_logs(
    job_id: uuid.UUID,
    tail: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> PlainTextResponse:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return PlainTextResponse(jobsvc.format_job_logs(db, job, tail=tail))


@router.get("/{job_id}/report.pdf")
def job_report(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> FileResponse:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    path = jobsvc.locate_report_pdf(job)
    if path is None:
        raise HTTPException(404, "质量报告尚未生成")
    return FileResponse(path, filename="质量报告.pdf", media_type="application/pdf")
