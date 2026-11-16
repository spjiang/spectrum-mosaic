from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.db import get_db
from app.models import Admin, Note
from app.schemas import NoteCreateIn, NoteOut, NoteUpdateIn
from app.services.audit import write_audit

router = APIRouter(prefix="/api/settings/notes", tags=["settings"])


@router.get("", response_model=list[NoteOut])
def list_notes(db: Session = Depends(get_db), _: Admin = Depends(get_current_admin)) -> list[Note]:
    return list(
        db.scalars(select(Note).where(Note.deleted_at.is_(None)).order_by(Note.updated_at.desc(), Note.id.desc())).all()
    )


@router.post("", response_model=NoteOut)
def create_note(
    body: NoteCreateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Note:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    row = Note(title=title, body=body.body or "")
    db.add(row)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=admin.username,
        action="note.create",
        object_type="note",
        object_id=str(row.id),
        detail=title,
    )
    return row


@router.patch("/{note_id}", response_model=NoteOut)
def update_note(
    note_id: int,
    body: NoteUpdateIn,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> Note:
    row = db.get(Note, note_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="注意事项不存在")
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    row.title = title
    row.body = body.body or ""
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    write_audit(
        db,
        actor=admin.username,
        action="note.update",
        object_type="note",
        object_id=str(note_id),
        detail=title,
    )
    return row


@router.delete("/{note_id}")
def delete_note(
    note_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
) -> dict[str, str]:
    row = db.get(Note, note_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="注意事项不存在")
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    write_audit(
        db,
        actor=admin.username,
        action="note.soft_delete",
        object_type="note",
        object_id=str(note_id),
        detail=row.title,
    )
    return {"status": "ok"}
