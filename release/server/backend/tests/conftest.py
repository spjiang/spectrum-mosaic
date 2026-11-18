from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import hash_password
from app.config import get_settings
from app.models import Admin, Customer, License, Release
from app.services.license_crypto import init_keys, sign_license, write_license_file


@pytest.fixture()
def db_engine():
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Generator[Session, None, None]:
    connection = db_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def keys_dir(tmp_path: Path) -> Path:
    return init_keys(tmp_path)[0].parent


@pytest.fixture()
def settings(keys_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KEYS_DIR", str(keys_dir))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def admin_user(db_session: Session) -> Admin:
    existing = db_session.scalar(select(Admin).limit(1))
    if existing is not None:
        return existing
    row = Admin(username="pytest-admin", password_hash=hash_password("pytest-pass"))
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def bound_license(
    db_session: Session,
    keys_dir: Path,
    settings,
    tmp_path: Path,
) -> tuple[License, str]:
    customer = Customer(name="单测客户", status="trial")
    release = Release(version="0.0.0-test", arch="amd64", status="success")
    db_session.add(customer)
    db_session.add(release)
    db_session.flush()

    machine_id = "d" * 64
    lic_uuid = f"test-{tmp_path.name}-{machine_id[:8]}"
    now = datetime.now(timezone.utc)
    payload = {
        "license_id": lic_uuid,
        "customer": customer.name,
        "product": settings.product_name,
        "release_version": release.version,
        "starts_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine_id": machine_id,
    }
    priv, _ = init_keys(keys_dir)
    doc = sign_license(payload, priv)
    lic_dir = tmp_path / "artifacts" / "licenses"
    lic_dir.mkdir(parents=True)
    out_path = lic_dir / f"{lic_uuid}.lic"
    write_license_file(out_path, doc)

    row = License(
        license_id=lic_uuid,
        customer_id=customer.id,
        release_id=release.id,
        starts_at=now,
        expires_at=now + timedelta(days=365),
        issued_at=now,
        file_path=str(out_path),
        file_checksum="",
        status="active",
        machine_id=machine_id,
    )
    db_session.add(row)
    db_session.flush()
    return row, machine_id