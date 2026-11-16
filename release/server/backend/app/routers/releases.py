from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.db import get_db
from app.models import Admin, Delivery, License, Release
from app.schemas import ReleaseArtifactOut, ReleaseCreateIn, ReleaseOut, ReleaseUpdateIn
from app.services.audit import write_audit
from app.services.build_runner import start_build
from app.services.release_artifacts import (
    ARTIFACT_FILES,
    download_filename,
    list_artifacts,
    resolve_artifact_file,
)

router = APIRouter(prefix="/api/releases", tags=["releases"])


@router.get("", response_model=list[ReleaseOut])
def list_releases(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> list[Release]:
    return list(
        db.scalars(select(Release).where(Release.deleted_at.is_(None)).order_by(Release.id.desc())).all()
    )


@router.post("", response_model=ReleaseOut)
def create_release(
    body: ReleaseCreateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Release:
    version = body.version.strip()
    arch = body.arch
    exists = db.scalar(
        select(Release).where(
            Release.version == version,
            Release.arch == arch,
            Release.deleted_at.is_(None),
        )
    )
    if exists:
        raise HTTPException(status_code=400, detail="同版本同架构已存在")
    row = Release(version=version, arch=arch, status="pending")
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="release.create", object_type="release", object_id=str(row.id))
    return row


@router.patch("/{release_id}", response_model=ReleaseOut)
def update_release(
    release_id: int,
    body: ReleaseUpdateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Release:
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if row.status == "running":
        raise HTTPException(status_code=409, detail="构建进行中，不能改版本号")
    version = body.version.strip()
    exists = db.scalar(
        select(Release).where(
            Release.version == version,
            Release.arch == row.arch,
            Release.id != release_id,
            Release.deleted_at.is_(None),
        )
    )
    if exists:
        raise HTTPException(status_code=400, detail="同版本同架构已存在")
    row.version = version
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="release.update", object_type="release", object_id=str(row.id))
    return row


@router.delete("/{release_id}")
def delete_release(
    release_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> dict[str, str]:
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if row.status == "running":
        raise HTTPException(status_code=409, detail="构建进行中，不能删除")
    lic_n = (
        db.scalar(
            select(func.count())
            .select_from(License)
            .where(License.release_id == release_id, License.deleted_at.is_(None))
        )
        or 0
    )
    del_n = (
        db.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(Delivery.release_id == release_id, Delivery.deleted_at.is_(None))
        )
        or 0
    )
    if lic_n or del_n:
        raise HTTPException(status_code=400, detail=f"版本下还有 {lic_n} 条许可、{del_n} 个交付包，请先删除")
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    write_audit(db, actor=admin.username, action="release.soft_delete", object_type="release", object_id=str(release_id))
    return {"status": "ok"}


@router.post("/{release_id}/build", response_model=ReleaseOut)
def trigger_build(
    release_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Release:
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if row.status == "running":
        raise HTTPException(status_code=409, detail="构建进行中")
    start_build(release_id)
    write_audit(db, actor=admin.username, action="release.build", object_type="release", object_id=str(release_id))
    db.refresh(row)
    return row


@router.get("/{release_id}/log")
def get_log(release_id: int, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if not row.log_path or not Path(row.log_path).exists():
        return PlainTextResponse("暂无日志")
    return PlainTextResponse(Path(row.log_path).read_text(encoding="utf-8", errors="replace"))


@router.get("/{release_id}/artifacts", response_model=list[ReleaseArtifactOut])
def list_release_artifacts(
    release_id: int, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)
) -> list[ReleaseArtifactOut]:
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return [ReleaseArtifactOut(**item) for item in list_artifacts(row)]


@router.get("/{release_id}/artifacts/{name}")
def download_release_artifact(
    release_id: int, name: str, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)
):
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    key = name.strip().lower()
    if key not in ARTIFACT_FILES:
        raise HTTPException(status_code=400, detail="未知镜像名")
    path = resolve_artifact_file(row, key)
    if path is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    return FileResponse(path, filename=download_filename(row, key))


@router.get("/{release_id}/artifact")
def download_artifact(release_id: int, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    """兼容旧入口：等同下载 worker。"""
    row = db.get(Release, release_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    path = resolve_artifact_file(row, "worker")
    if path is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    return FileResponse(path, filename=download_filename(row, "worker"))
