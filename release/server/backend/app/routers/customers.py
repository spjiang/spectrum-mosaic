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

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
def list_customers(
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> list[Customer]:
    return list(
        db.scalars(select(Customer).where(Customer.deleted_at.is_(None)).order_by(Customer.id.desc())).all()
    )


@router.post("", response_model=CustomerOut)
def create_customer(
    body: CustomerIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Customer:
    if body.status not in {"trial", "formal", "disabled"}:
        raise HTTPException(status_code=400, detail="status 无效")
    row = Customer(name=body.name, contact=body.contact, status=body.status, notes=body.notes)
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="customer.create", object_type="customer", object_id=str(row.id))
    return row


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    body: CustomerIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Customer:
    row = db.get(Customer, customer_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    if body.status not in {"trial", "formal", "disabled"}:
        raise HTTPException(status_code=400, detail="status 无效")
    row.name = body.name
    row.contact = body.contact
    row.status = body.status
    row.notes = body.notes
    db.commit()
    db.refresh(row)
    write_audit(db, actor=admin.username, action="customer.update", object_type="customer", object_id=str(row.id))
    return row


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
