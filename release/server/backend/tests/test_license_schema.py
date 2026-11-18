from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas import LicenseCreateIn, LicenseUpdateIn
from app.services.license_crypto import LicenseError, init_keys, sign_license, verify_license


def test_create_in_allows_empty_machine_id():
    row = LicenseCreateIn(
        customer_id=1,
        release_id=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        machine_id="",
    )
    assert row.machine_id == ""


def test_create_in_normalizes_machine_id():
    mid = "a" * 64
    row = LicenseCreateIn(
        customer_id=1,
        release_id=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        machine_id=mid.upper(),
    )
    assert row.machine_id == mid


def test_create_in_rejects_bad_machine_id():
    with pytest.raises(ValidationError):
        LicenseCreateIn(
            customer_id=1,
            release_id=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            machine_id="short",
        )


def test_update_in_empty_machine_id_means_unbind():
    row = LicenseUpdateIn(status="active", machine_id="")
    assert row.machine_id == ""


def test_sign_unbound_license_omits_machine_id(tmp_path):
    priv, pub = init_keys(tmp_path)
    now = datetime.now(timezone.utc)
    payload = {
        "license_id": "lic-unbound",
        "customer": "测试客户",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = sign_license(payload, priv)
    assert "machine_id" not in doc["payload"]
    verify_license(doc, pub)