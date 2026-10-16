from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
    pool_timeout=10,
    pool_recycle=120,
    connect_args={"connect_timeout": 8},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        try:
            db.execute(text("SELECT 1"))
        except OperationalError:
            db.close()
            engine.dispose()
            db = SessionLocal()
            db.execute(text("SELECT 1"))
        yield db
    finally:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        db.close()
