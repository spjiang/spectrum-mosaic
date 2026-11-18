from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Customer, LeaseClaim, SystemSetting
from app.routers.licenses import check_license, list_lease_claims
from app.schemas import LicenseCheckIn
from app.services.system_settings import KEY_LEASE_HOURS


def _request(ip: str = "203.0.113.10"):
    req = MagicMock()
    req.headers = {"x-real-ip": ip}
    req.client = MagicMock(host=ip)
    return req


def test_check_license_records_lease_claim(db_session, bound_license, settings, admin_user):
    lic, machine_id = bound_license
    body = LicenseCheckIn(license_id=lic.license_id, machine_id=machine_id)
    doc = check_license(body, _request(), db_session, settings)
    assert "payload" in doc
    claims = list(db_session.scalars(select(LeaseClaim).where(LeaseClaim.license_id == lic.license_id)).all())
    assert len(claims) == 1
    assert claims[0].ok is True
    assert claims[0].detail == "已签发租约"
    assert claims[0].client_ip == "203.0.113.10"
    assert claims[0].lease_expires_at is not None


def test_check_license_rejected_logs_failure(db_session, bound_license, settings):
    lic, _machine_id = bound_license
    body = LicenseCheckIn(license_id=lic.license_id, machine_id="e" * 64)
    with pytest.raises(HTTPException) as exc:
        check_license(body, _request("198.51.100.1"), db_session, settings)
    assert exc.value.status_code == 403
    claim = db_session.scalar(select(LeaseClaim).order_by(LeaseClaim.id.desc()))
    assert claim is not None
    assert claim.ok is False


def test_list_lease_claims_pagination(db_session, bound_license, settings, admin_user):
    lic, machine_id = bound_license
    for i in range(3):
        check_license(
            LicenseCheckIn(license_id=lic.license_id, machine_id=machine_id),
            _request(f"203.0.113.{i + 1}"),
            db_session,
            settings,
        )
    n = db_session.scalar(
        select(func.count()).select_from(LeaseClaim).where(LeaseClaim.license_id == lic.license_id)
    )
    assert n == 3

    page1 = list_lease_claims(page=1, page_size=2, db=db_session, _=admin_user)
    assert page1.page_size == 2
    assert len(page1.items) == 2
    assert page1.items[0].created_at >= page1.items[1].created_at


def test_check_license_uses_configured_lease_hours(db_session, bound_license, settings):
    row = db_session.get(SystemSetting, KEY_LEASE_HOURS)
    if row is None:
        db_session.add(
            SystemSetting(
                key=KEY_LEASE_HOURS,
                value="3",
                label="租约有效期（小时）",
                description="",
                used_by="",
            )
        )
    else:
        row.value = "3"
    db_session.flush()
    lic, machine_id = bound_license
    before = datetime.now(timezone.utc)
    doc = check_license(
        LicenseCheckIn(license_id=lic.license_id, machine_id=machine_id),
        _request(),
        db_session,
        settings,
    )
    expires = datetime.strptime(doc["payload"]["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    delta = expires - before
    assert timedelta(hours=2, minutes=59) <= delta <= timedelta(hours=3, minutes=1)


def test_check_license_customer_lease_hours_override_global(db_session, bound_license, settings):
    lic, machine_id = bound_license
    customer = db_session.get(Customer, lic.customer_id)
    assert customer is not None
    customer.lease_hours = "5"
    db_session.flush()
    before = datetime.now(timezone.utc)
    doc = check_license(
        LicenseCheckIn(license_id=lic.license_id, machine_id=machine_id),
        _request(),
        db_session,
        settings,
    )
    expires = datetime.strptime(doc["payload"]["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    delta = expires - before
    assert timedelta(hours=4, minutes=59) <= delta <= timedelta(hours=5, minutes=1)