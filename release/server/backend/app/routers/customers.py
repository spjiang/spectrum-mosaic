from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.db import get_db
from app.models import Admin, Customer, Delivery, License
from app.schemas import CustomerIn, CustomerOut
from app.services.audit import write_audit
from app.services.system_settings import (
    format_hours,
    get_clock_tolerance_hours,
    get_lease_hours,
    get_lease_interval_hours,
)

router = APIRouter(prefix="/api/customers", tags=["customers"])


def _format_optional_hours(raw: str, label: str, *, allow_zero: bool) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        hours = float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{label}须为数字（小时），留空则使用全局配置") from exc
    if hours < 0 or (hours == 0 and not allow_zero):
        raise HTTPException(status_code=400, detail=f"{label}必须大于 0，留空则使用全局配置")
    if allow_zero and hours > 24 * 30:
        raise HTTPException(status_code=400, detail=f"{label}过大（上限 720 小时）")
    if not allow_zero and hours > 24 * 365:
        raise HTTPException(status_code=400, detail=f"{label}过大（上限 8760 小时）")
    return format_hours(hours)


def _apply_runtime(row: Customer, body: CustomerIn, db: Session) -> None:
    clock = _format_optional_hours(body.clock_tolerance_hours, "回拨容差", allow_zero=True)
    lease = _format_optional_hours(body.lease_hours, "租约有效期", allow_zero=False)
    interval = _format_optional_hours(body.lease_interval_hours, "租约领取间隔", allow_zero=False)
    lease_hours = float(lease) if lease else get_lease_hours(db)
    interval_hours = float(interval) if interval else get_lease_interval_hours(db)
    if interval_hours >= lease_hours:
        raise HTTPException(status_code=400, detail="租约领取间隔必须小于租约有效期")
    row.clock_tolerance_hours = clock
    row.lease_hours = lease
    row.lease_interval_hours = interval


def _to_out(row: Customer, db: Session) -> CustomerOut:
    data = CustomerOut.model_validate(row)
    return data.model_copy(
        update={
            "effective_clock_tolerance_hours": format_hours(get_clock_tolerance_hours(db, customer=row)),
            "effective_lease_hours": format_hours(get_lease_hours(db, customer=row)),
            "effective_lease_interval_hours": format_hours(get_lease_interval_hours(db, customer=row)),
        }
    )


@router.get("", response_model=list[CustomerOut])
def list_customers(
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> list[CustomerOut]:
    rows = list(db.scalars(select(Customer).where(Customer.deleted_at.is_(None)).order_by(Customer.id.desc())).all())
    return [_to_out(row, db) for row in rows]


@router.post("", response_model=CustomerOut)
def create_customer(
    body: CustomerIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> CustomerOut:
    if body.status not in {"trial", "formal", "disabled"}:
        raise HTTPException(status_code=400, detail="status 无效")
    row = Customer(name=body.name, contact=body.contact, status=body.status, notes=body.notes)
    _apply_runtime(row, body, db)
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="customer.create", object_type="customer", object_id=str(row.id))
    return _to_out(row, db)


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    body: CustomerIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> CustomerOut:
    row = db.get(Customer, customer_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if body.status not in {"trial", "formal", "disabled"}:
        raise HTTPException(status_code=400, detail="status 无效")
    row.name = body.name
    row.contact = body.contact
    row.status = body.status
    row.notes = body.notes
    _apply_runtime(row, body, db)
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="customer.update", object_type="customer", object_id=str(row.id))
    return _to_out(row, db)


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> dict[str, str]:
    row = db.get(Customer, customer_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    lic_n = (
        db.scalar(
            select(func.count())
            .select_from(License)
            .where(License.customer_id == customer_id, License.deleted_at.is_(None))
        )
        or 0
    )
    del_n = (
        db.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(Delivery.customer_id == customer_id, Delivery.deleted_at.is_(None))
        )
        or 0
    )
    if lic_n or del_n:
        raise HTTPException(status_code=400, detail=f"客户下还有 {lic_n} 条许可、{del_n} 个交付包，请先删除")
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    write_audit(db, actor=admin.username, action="customer.soft_delete", object_type="customer", object_id=str(customer_id))
    return {"status": "ok"}
