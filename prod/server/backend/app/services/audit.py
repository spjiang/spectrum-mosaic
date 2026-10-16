"""操作审计。只记谁做了什么，不记密码。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record_audit(
    db: Session,
    user_id: int | None,
    action: str,
    detail: dict[str, Any] | None = None,
) -> None:
    db.add(AuditLog(user_id=user_id, action=action, detail=detail or {}))
    db.commit()


def list_audits(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AuditLog, User.username)
        .outerjoin(User, AuditLog.user_id == User.id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    out = []
    for log, username in rows:
        out.append(
            {
                "id": log.id,
                "username": username,
                "action": log.action,
                "detail": log.detail or {},
                "created_at": log.created_at,
            }
        )
    return out
