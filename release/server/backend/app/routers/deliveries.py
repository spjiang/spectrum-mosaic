from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Admin, Customer, Delivery, License, Release
from app.schemas import DeliveryContentsOut, DeliveryCreateIn, DeliveryEntryOut, DeliveryOut
from app.services.audit import write_audit
from app.services.packager import build_delivery_zip

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"])


@router.get("", response_model=list[DeliveryOut])
def list_deliveries(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> list[Delivery]:
    return list(
        db.scalars(select(Delivery).where(Delivery.deleted_at.is_(None)).order_by(Delivery.id.desc())).all()
    )


@router.post("", response_model=DeliveryOut)
def create_delivery(
    body: DeliveryCreateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> Delivery:
    customer = db.get(Customer, body.customer_id)
    release = db.get(Release, body.release_id)
    license_row = db.get(License, body.license_id)
    if (
        customer is None
        or release is None
        or license_row is None
        or customer.deleted_at is not None
        or release.deleted_at is not None
        or license_row.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="客户、版本或许可不存在")
    if release.status != "success":
        raise HTTPException(status_code=400, detail="版本尚未构建成功，不能打交付包")
    if license_row.status != "active":
        raise HTTPException(status_code=400, detail="许可已停用")
    if license_row.customer_id != customer.id or license_row.release_id != release.id:
        raise HTTPException(status_code=400, detail="许可与客户/版本不匹配")

    try:
        zip_path, checksum = build_delivery_zip(
            settings=settings,
            customer=customer,
            release=release,
            license_row=license_row,
            include_image_tar=body.include_image_tar,
            db=db,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = Delivery(
        customer_id=customer.id,
        release_id=release.id,
        license_id=license_row.id,
        package_path=str(zip_path),
        checksum=checksum,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=admin.username,
        action="delivery.create",
        object_type="delivery",
        object_id=str(row.id),
        detail=zip_path.name,
    )
    return row


@router.delete("/{delivery_id}")
def delete_delivery(
    delivery_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> dict[str, str]:
    row = db.get(Delivery, delivery_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="交付包不存在")
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    write_audit(db, actor=admin.username, action="delivery.soft_delete", object_type="delivery", object_id=str(delivery_id))
    return {"status": "ok"}


def _local_delivery_paths(package_path: Path) -> tuple[str, str]:
    """Host-facing paths under the spectrum-mosaic repo (compose mounts ../artifacts)."""
    import os

    name = package_path.name
    host_root = (os.environ.get("ARTIFACTS_HOST_DIR") or "").strip()
    if host_root:
        local_dir = str(Path(host_root) / "deliveries")
        local_path = str(Path(host_root) / "deliveries" / name)
        return local_dir, local_path
    return "release/artifacts/deliveries", f"release/artifacts/deliveries/{name}"


@router.get("/{delivery_id}/contents", response_model=DeliveryContentsOut)
def delivery_contents(delivery_id: int, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> DeliveryContentsOut:
    row = db.get(Delivery, delivery_id)
    if row is None or row.deleted_at is not None or not row.package_path or not Path(row.package_path).exists():
        raise HTTPException(status_code=404, detail="交付包不存在")
    path = Path(row.package_path)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            entries: list[DeliveryEntryOut] = []
            total = 0
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                name = info.filename
                is_dir = name.endswith("/")
                size = 0 if is_dir else int(info.file_size)
                total += size
                entries.append(DeliveryEntryOut(path=name, size=size, is_dir=is_dir))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="交付包不是有效的 zip") from exc
    files = [e for e in entries if not e.is_dir]
    local_dir, local_path = _local_delivery_paths(path)
    return DeliveryContentsOut(
        delivery_id=row.id,
        package_name=path.name,
        checksum=row.checksum,
        file_count=len(files),
        total_size=total,
        package_path=str(path),
        local_dir=local_dir,
        local_path=local_path,
        entries=entries,
    )


@router.get("/{delivery_id}/download")
def download_delivery(delivery_id: int, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    row = db.get(Delivery, delivery_id)
    if row is None or row.deleted_at is not None or not row.package_path or not Path(row.package_path).exists():
        raise HTTPException(status_code=404, detail="交付包不存在")
    return FileResponse(row.package_path, filename=Path(row.package_path).name)
