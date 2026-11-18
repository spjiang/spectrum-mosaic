from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Admin, Customer, Delivery, LeaseClaim, License, Release
from app.schemas import LeaseClaimPage, LicenseCheckIn, LicenseCreateIn, LicenseOut, LicenseUpdateIn
from app.services.audit import write_audit
from app.services.license_crypto import (
    PRIVATE_NAME,
    LicenseError,
    init_keys,
    normalize_machine_id,
    sign_lease,
    sign_license,
    write_license_file,
)
from app.services.system_settings import get_lease_hours

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/licenses", tags=["licenses"])
public_router = APIRouter(tags=["licenses"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fmt(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_window(starts_at: datetime, expires_at: datetime) -> None:
    if starts_at >= expires_at:
        raise HTTPException(status_code=400, detail="生效时间必须早于到期时间")


def _client_ip(request: Request) -> str:
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real[:128]
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()[:128]
    if request.client is not None:
        return request.client.host[:128]
    return ""


def _record_claim(
    db: Session,
    *,
    license_id: str,
    customer_name: str,
    machine_id: str,
    ok: bool,
    detail: str,
    lease_expires_at: datetime | None,
    client_ip: str,
) -> None:
    try:
        db.add(
            LeaseClaim(
                license_id=license_id[:128],
                customer_name=customer_name[:200],
                machine_id=machine_id[:128],
                ok=ok,
                detail=detail[:500],
                lease_expires_at=lease_expires_at,
                client_ip=client_ip,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("lease claim log failed")


def _customer_name(db: Session, row: License) -> str:
    customer = db.get(Customer, row.customer_id)
    return customer.name if customer is not None else ""


@public_router.post("/api/license/check")
def check_license(
    body: LicenseCheckIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """客户 worker 联网换取短时租约。不要求管理员登录。每次领取都写入 lease_claims。"""
    ip = _client_ip(request)
    license_id = body.license_id.strip()
    try:
        machine_id = normalize_machine_id(body.machine_id)
    except LicenseError as exc:
        _record_claim(
            db,
            license_id=license_id,
            customer_name="",
            machine_id=body.machine_id,
            ok=False,
            detail=str(exc),
            lease_expires_at=None,
            client_ip=ip,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = db.scalar(select(License).where(License.license_id == license_id, License.deleted_at.is_(None)))
    if row is None:
        _record_claim(
            db,
            license_id=license_id,
            customer_name="",
            machine_id=machine_id,
            ok=False,
            detail="许可不存在",
            lease_expires_at=None,
            client_ip=ip,
        )
        raise HTTPException(status_code=404, detail="许可不存在")
    customer_name = _customer_name(db, row)

    def reject(status: int, detail: str) -> None:
        _record_claim(
            db,
            license_id=row.license_id,
            customer_name=customer_name,
            machine_id=machine_id,
            ok=False,
            detail=detail,
            lease_expires_at=None,
            client_ip=ip,
        )
        raise HTTPException(status_code=status, detail=detail)

    if row.status != "active":
        reject(403, "许可已停用")
    now = datetime.now(timezone.utc)
    if now < _as_utc(row.starts_at):
        reject(403, "许可尚未生效")
    if now > _as_utc(row.expires_at):
        reject(403, "许可已过期")
    bound = (row.machine_id or "").strip().lower()
    if not bound:
        reject(403, "该许可未绑定机器")
    if bound != machine_id:
        reject(403, "许可与当前机器不匹配")
    customer = db.get(Customer, row.customer_id)
    lease_end = now + timedelta(hours=get_lease_hours(db, customer=customer))
    if lease_end > _as_utc(row.expires_at):
        lease_end = _as_utc(row.expires_at)
    try:
        priv, _ = init_keys(Path(settings.keys_dir))
        signed = sign_lease(
            {
                "license_id": row.license_id,
                "machine_id": machine_id,
                "issued_at": _fmt(now),
                "expires_at": _fmt(lease_end),
            },
            priv,
        )
    except LicenseError as exc:
        _record_claim(
            db,
            license_id=row.license_id,
            customer_name=customer_name,
            machine_id=machine_id,
            ok=False,
            detail=str(exc),
            lease_expires_at=None,
            client_ip=ip,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _record_claim(
        db,
        license_id=row.license_id,
        customer_name=customer_name,
        machine_id=machine_id,
        ok=True,
        detail="已签发租约",
        lease_expires_at=lease_end,
        client_ip=ip,
    )
    return signed


@router.get("/lease-claims", response_model=LeaseClaimPage)
def list_lease_claims(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> LeaseClaimPage:
    total = db.scalar(select(func.count()).select_from(LeaseClaim)) or 0
    rows = list(
        db.scalars(
            select(LeaseClaim)
            .order_by(LeaseClaim.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return LeaseClaimPage(total=total, page=page, page_size=page_size, items=rows)


@router.get("", response_model=list[LicenseOut])
def list_licenses(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> list[License]:
    return list(
        db.scalars(select(License).where(License.deleted_at.is_(None)).order_by(License.id.desc())).all()
    )


@router.post("", response_model=LicenseOut)
def create_license(
    body: LicenseCreateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> License:
    customer = db.get(Customer, body.customer_id)
    release = db.get(Release, body.release_id)
    if customer is None or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if release is None or release.deleted_at is not None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if customer.status == "disabled":
        raise HTTPException(status_code=400, detail="客户已停用")

    keys_dir = Path(settings.keys_dir)
    try:
        priv, _ = init_keys(keys_dir)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"密钥初始化失败: {exc}") from exc
    if not (keys_dir / PRIVATE_NAME).exists():
        raise HTTPException(status_code=503, detail="签发私钥不存在")

    license_id = str(uuid.uuid4())
    issued_at = datetime.now(timezone.utc)
    starts_at = _as_utc(body.starts_at) if body.starts_at is not None else issued_at
    expires_at = _as_utc(body.expires_at)
    _ensure_window(starts_at, expires_at)

    machine_id = (body.machine_id or "").strip().lower()
    payload = {
        "license_id": license_id,
        "customer": customer.name,
        "product": settings.product_name,
        "release_version": release.version,
        "starts_at": _fmt(starts_at),
        "expires_at": _fmt(expires_at),
        "issued_at": _fmt(issued_at),
    }
    if machine_id:
        try:
            machine_id = normalize_machine_id(machine_id)
        except LicenseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload["machine_id"] = machine_id
    try:
        doc = sign_license(payload, priv)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    out_path = Path(settings.artifacts_dir) / "licenses" / f"{license_id}.lic"
    write_license_file(out_path, doc)
    checksum = hashlib.sha256(out_path.read_bytes()).hexdigest()

    row = License(
        license_id=license_id,
        customer_id=customer.id,
        release_id=release.id,
        starts_at=starts_at,
        expires_at=expires_at,
        issued_at=issued_at,
        file_path=str(out_path),
        file_checksum=checksum,
        status="active",
        machine_id=machine_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=admin.username,
        action="license.issue",
        object_type="license",
        object_id=license_id,
        detail=f"customer={customer.name} version={release.version}",
    )
    return row


@router.patch("/{license_pk}", response_model=LicenseOut)
def update_license(
    license_pk: int,
    body: LicenseUpdateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
    settings: Settings = Depends(get_settings),
) -> License:
    row = db.get(License, license_pk)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="许可不存在")
    if body.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="status 无效")

    next_machine = (row.machine_id or "").strip().lower()
    if body.machine_id is not None:
        if body.machine_id.strip():
            try:
                next_machine = normalize_machine_id(body.machine_id)
            except LicenseError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            next_machine = ""
    resign = (
        body.starts_at is not None
        or body.expires_at is not None
        or next_machine != (row.machine_id or "").strip().lower()
    )
    if resign:
        customer = db.get(Customer, row.customer_id)
        release = db.get(Release, row.release_id)
        if customer is None or release is None or customer.deleted_at is not None or release.deleted_at is not None:
            raise HTTPException(status_code=400, detail="关联客户或版本不存在")
        starts_at = _as_utc(body.starts_at) if body.starts_at is not None else _as_utc(row.starts_at)
        expires_at = _as_utc(body.expires_at) if body.expires_at is not None else _as_utc(row.expires_at)
        _ensure_window(starts_at, expires_at)
        issued_at = datetime.now(timezone.utc)
        priv, _ = init_keys(Path(settings.keys_dir))
        payload = {
            "license_id": row.license_id,
            "customer": customer.name,
            "product": settings.product_name,
            "release_version": release.version,
            "starts_at": _fmt(starts_at),
            "expires_at": _fmt(expires_at),
            "issued_at": _fmt(issued_at),
        }
        if next_machine:
            payload["machine_id"] = next_machine
        try:
            doc = sign_license(payload, priv)
        except LicenseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        out_path = Path(row.file_path) if row.file_path else Path(settings.artifacts_dir) / "licenses" / f"{row.license_id}.lic"
        write_license_file(out_path, doc)
        row.starts_at = starts_at
        row.expires_at = expires_at
        row.issued_at = issued_at
        row.file_path = str(out_path)
        row.file_checksum = hashlib.sha256(out_path.read_bytes()).hexdigest()
        row.machine_id = next_machine

    row.status = body.status
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="license.update", object_type="license", object_id=row.license_id)
    return row


@router.delete("/{license_pk}")
def delete_license(
    license_pk: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> dict[str, str]:
    row = db.get(License, license_pk)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="许可不存在")
    del_n = (
        db.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(Delivery.license_id == license_pk, Delivery.deleted_at.is_(None))
        )
        or 0
    )
    if del_n:
        raise HTTPException(status_code=400, detail=f"许可下还有 {del_n} 个交付包，请先删除")
    lid = row.license_id
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    write_audit(db, actor=admin.username, action="license.soft_delete", object_type="license", object_id=lid)
    return {"status": "ok"}


@router.post("/{license_pk}/disable", response_model=LicenseOut)
def disable_license(
    license_pk: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> License:
    row = db.get(License, license_pk)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="许可不存在")
    row.status = "disabled"
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="license.disable", object_type="license", object_id=row.license_id)
    return row


@router.get("/{license_pk}/download")
def download_license(license_pk: int, db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)):
    row = db.get(License, license_pk)
    if row is None or row.deleted_at is not None or not row.file_path or not Path(row.file_path).exists():
        raise HTTPException(status_code=404, detail="许可文件不存在")
    return FileResponse(row.file_path, filename=f"{row.license_id}.lic")
