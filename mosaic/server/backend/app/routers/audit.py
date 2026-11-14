"""管理员查看操作审计。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.db import get_db
from app.models import User
from app.services.audit import list_audits

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditOut(BaseModel):
    id: int
    username: str | None = None
    action: str
    detail: dict[str, Any]
    created_at: datetime | None = None


@router.get("", response_model=list[AuditOut])
def get_audits(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
) -> list[dict[str, Any]]:
    return list_audits(db)
