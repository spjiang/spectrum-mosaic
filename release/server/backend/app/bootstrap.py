from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import Settings
from app.models import Admin, Note
from app.services.license_crypto import init_keys
from app.services.system_settings import ensure_system_settings

log = logging.getLogger("uvicorn.error")


def apply_sql_migration(db: Session, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    db.execute(text(sql))
    db.commit()


def ensure_admin(db: Session, settings: Settings) -> None:
    exists = db.scalar(select(Admin).where(Admin.username == settings.bootstrap_admin_user))
    if exists:
        return
    db.add(
        Admin(
            username=settings.bootstrap_admin_user,
            password_hash=hash_password(settings.bootstrap_admin_password),
        )
    )
    db.commit()


def migrate_file_notes_if_needed(db: Session, settings: Settings) -> None:
    alive = db.scalar(select(Note.id).where(Note.deleted_at.is_(None)).limit(1))
    if alive is not None:
        return
    path = Path(settings.artifacts_dir) / "notes" / "注意事项.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return
    db.add(Note(title="迁移自文件 · 注意事项", body=text + "\n"))
    db.commit()
    log.info("imported legacy notes file into notes table")


def bootstrap(db: Session, settings: Settings, migration_path: Path) -> None:
    migrations_dir = migration_path.parent if migration_path.name.endswith(".sql") else migration_path
    # Apply each file independently so an existing DB can still pick up later ALTERs.
    for name in (
        "001_init.sql",
        "002_soft_delete.sql",
        "003_starts_at.sql",
        "004_notes.sql",
        "005_system_settings.sql",
        "006_release_arch.sql",
        "007_license_machine.sql",
        "008_lease_claims.sql",
    ):
        path = migrations_dir / name
        if not path.exists():
            continue
        try:
            apply_sql_migration(db, path)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("migration %s skipped/failed (ok if already applied): %s", name, exc)
    ensure_admin(db, settings)
    try:
        ensure_system_settings(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("system settings seed skipped: %s", exc)
    try:
        migrate_file_notes_if_needed(db, settings)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.warning("notes file migrate skipped: %s", exc)
    init_keys(Path(settings.keys_dir))
