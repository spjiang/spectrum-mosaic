"""增量拼图入口。收片入库，并在测区绑定的那一条任务上拼图。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.services import incremental as svc
from app.services.audit import record_audit
from app.services.deps import get_mq
from app.services.mq import MQPublisher

router = APIRouter(prefix="/api/incremental", tags=["incremental"])


class ProjectCreate(BaseModel):
    name: str
    input_dir: str
    output_root: str
    auto_run: bool = False
    scan_interval_minutes: int = 5


class ProjectPatch(BaseModel):
    name: str | None = None
    auto_run: bool | None = None
    scan_interval_minutes: int | None = None


def _summary(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "input_dir": row.input_dir,
        "inbox_dir": row.inbox_dir,
        "output_root": row.output_root,
        "auto_run": row.auto_run,
        "scan_interval_minutes": row.scan_interval_minutes,
        "latest_shot_index": row.latest_shot_index,
        "latest_filename": row.latest_filename,
        "current_output_dir": row.current_output_dir,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/projects")
def list_projects(
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "admin")),
) -> dict:
    rows, total = svc.page_projects(db, page, page_size)
    return {"items": [_summary(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/projects")
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict:
    row = svc.create_project(
        db,
        settings,
        name=body.name,
        input_dir=body.input_dir,
        output_root=body.output_root,
        auto_run=body.auto_run,
        scan_interval_minutes=body.scan_interval_minutes,
        user_id=user.id,
    )
    record_audit(
        db,
        user.id,
        "incremental.create",
        {
            "id": str(row.id),
            "input_dir": row.input_dir,
            "inbox_dir": row.inbox_dir,
            "auto_run": row.auto_run,
            "scan_interval_minutes": row.scan_interval_minutes,
        },
    )
    return svc.project_detail(db, row)


@router.get("/projects/{project_id}")
def project_detail(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("executor", "admin")),
) -> dict:
    return svc.project_detail(db, svc.get_project(db, project_id))


@router.patch("/projects/{project_id}")
def patch_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict:
    row = svc.update_project(
        db,
        svc.get_project(db, project_id),
        name=body.name,
        auto_run=body.auto_run,
        scan_interval_minutes=body.scan_interval_minutes,
    )
    record_audit(db, user.id, "incremental.update", {"id": str(row.id), "auto_run": row.auto_run})
    return svc.project_detail(db, row)


@router.delete("/projects/{project_id}", status_code=204, response_model=None)
def delete_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("executor", "admin")),
) -> None:
    row = svc.get_project(db, project_id)
    name = row.name
    svc.delete_project(db, row)
    record_audit(db, user.id, "incremental.delete", {"id": str(project_id), "name": name})


@router.post("/projects/{project_id}/scan")
def scan_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict:
    row = svc.get_project(db, project_id)
    stats = svc.sweep(db, row)
    job = None
    if row.auto_run:
        job = svc.launch_mosaic(db, settings, mq, row, user_id=user.id, manual=False)
    if job is not None:
        record_audit(db, user.id, "incremental.run", {"id": str(row.id), "job_id": str(job.id), "auto": True})
    detail = svc.project_detail(db, row)
    detail["scan"] = stats
    detail["conflicts"] = stats["conflict"]
    detail["started_job_id"] = str(job.id) if job is not None else None
    return detail


@router.post("/projects/{project_id}/run")
def run_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> dict:
    row = svc.get_project(db, project_id)
    stats = svc.sweep(db, row)
    job = svc.launch_mosaic(db, settings, mq, row, user_id=user.id, manual=True)
    record_audit(db, user.id, "incremental.run", {"id": str(row.id), "job_id": str(job.id), "auto": False})
    detail = svc.project_detail(db, row)
    detail["conflicts"] = stats["conflict"]
    detail["started_job_id"] = str(job.id)
    return detail
