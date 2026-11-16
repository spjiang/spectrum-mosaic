from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.license_crypto import LicenseError, init_keys, sign_license, verify_license


def test_sign_and_verify_roundtrip(tmp_path: Path):
    priv, pub = init_keys(tmp_path)
    now = datetime.now(timezone.utc)
    payload = {
        "license_id": "lic-1",
        "customer": "试用单位",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = sign_license(payload, priv)
    out = verify_license(doc, pub)
    assert out["license_id"] == "lic-1"


def test_expired_rejected(tmp_path: Path):
    priv, pub = init_keys(tmp_path)
    payload = {
        "license_id": "lic-2",
        "customer": "试用单位",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": "2019-01-01T00:00:00Z",
        "expires_at": "2020-01-01T00:00:00Z",
        "issued_at": "2019-01-01T00:00:00Z",
    }
    doc = sign_license(payload, priv)
    try:
        verify_license(doc, pub)
        assert False, "expected LicenseError"
    except LicenseError as e:
        assert "过期" in str(e)


def test_not_yet_effective_rejected(tmp_path: Path):
    priv, pub = init_keys(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "license_id": "lic-3",
        "customer": "试用单位",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (future + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = sign_license(payload, priv)
    try:
        verify_license(doc, pub)
        assert False, "expected LicenseError"
    except LicenseError as e:
        assert "尚未生效" in str(e)
