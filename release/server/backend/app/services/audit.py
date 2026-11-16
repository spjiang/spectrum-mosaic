from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    detail: str = "",
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            detail=detail,
        )
    )
    db.commit()
