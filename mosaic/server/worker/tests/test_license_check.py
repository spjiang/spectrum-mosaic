import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from ms_mosaic.license_check import (
    LeaseNetworkError,
    LeaseRejected,
    LicenseError,
    check_clock_rollback,
    clock_rollback_tolerance,
    enforce_license_or_raise,
    load_clock_state,
    machine_fingerprint,
    verify_license_doc,
)


def _sign(payload: dict, signing: SigningKey) -> dict:
    import base64

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signed = signing.sign(body)
    return {"payload": payload, "signature": base64.b64encode(signed.signature).decode("ascii")}


def test_verify_ok():
    signing = SigningKey.generate()
    now = datetime.now(timezone.utc)
    payload = {
        "license_id": "a",
        "customer": "c",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = _sign(payload, signing)
    assert verify_license_doc(doc, signing.verify_key.encode())["license_id"] == "a"


def test_expired():
    signing = SigningKey.generate()
    payload = {
        "license_id": "a",
        "customer": "c",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": "2019-01-01T00:00:00Z",
        "expires_at": "2020-01-01T00:00:00Z",
        "issued_at": "2019-01-01T00:00:00Z",
    }
    doc = _sign(payload, signing)
    with pytest.raises(LicenseError, match="过期"):
        verify_license_doc(doc, signing.verify_key.encode())


def test_not_yet_effective():
    signing = SigningKey.generate()
    future = datetime.now(timezone.utc) + timedelta(days=3)
    payload = {
        "license_id": "a",
        "customer": "c",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": future.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (future + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = _sign(payload, signing)
    with pytest.raises(LicenseError, match="尚未生效"):
        verify_license_doc(doc, signing.verify_key.encode())


def test_enforce_skipped_without_env(monkeypatch):
    monkeypatch.delenv("MOSAIC_REQUIRE_LICENSE", raising=False)
    enforce_license_or_raise()


def test_enforce_requires_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MOSAIC_REQUIRE_LICENSE", "1")
    monkeypatch.setenv("LICENSE_PATH", str(tmp_path / "missing.lic"))
    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(tmp_path / "missing.pem"))
    with pytest.raises(LicenseError):
        enforce_license_or_raise()


def test_clock_first_seen_writes_state(tmp_path: Path):
    clock = tmp_path / "clock.json"
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    check_clock_rollback(license_id="lic-a", now=now, path=clock)
    state = load_clock_state(clock)
    assert state is not None
    assert state["license_id"] == "lic-a"
    assert state["last_seen_at"] == "2026-11-14T12:00:00Z"


def test_clock_rollback_beyond_tolerance_rejected(tmp_path: Path):
    clock = tmp_path / "clock.json"
    first = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    check_clock_rollback(license_id="lic-a", now=first, path=clock, tolerance=timedelta(hours=1))
    rolled = first - timedelta(hours=2)
    with pytest.raises(LicenseError, match="时钟回拨"):
        check_clock_rollback(license_id="lic-a", now=rolled, path=clock, tolerance=timedelta(hours=1))


def test_clock_tolerance_from_env(monkeypatch):
    monkeypatch.setenv("LICENSE_CLOCK_TOLERANCE_HOURS", "3")
    assert clock_rollback_tolerance() == timedelta(hours=3)
    monkeypatch.setenv("LICENSE_CLOCK_TOLERANCE_HOURS", "bad")
    assert clock_rollback_tolerance() == timedelta(hours=1)


def test_clock_rollback_within_tolerance_ok(tmp_path: Path):
    clock = tmp_path / "clock.json"
    first = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    check_clock_rollback(license_id="lic-a", now=first, path=clock)
    slight = first - timedelta(minutes=30)
    check_clock_rollback(license_id="lic-a", now=slight, path=clock)
    state = load_clock_state(clock)
    assert state is not None
    assert state["last_seen_at"] == "2026-11-14T11:30:00Z"


def test_clock_new_license_resets(tmp_path: Path):
    clock = tmp_path / "clock.json"
    first = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    check_clock_rollback(license_id="lic-a", now=first, path=clock)
    earlier = first - timedelta(days=30)
    check_clock_rollback(license_id="lic-b", now=earlier, path=clock)
    state = load_clock_state(clock)
    assert state is not None
    assert state["license_id"] == "lic-b"
    assert state["last_seen_at"] == "2026-10-15T12:00:00Z"


def test_enforce_clock_rollback(monkeypatch, tmp_path: Path):
    signing = SigningKey.generate()
    now = datetime.now(timezone.utc)
    payload = {
        "license_id": "lic-roll",
        "customer": "c",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    doc = _sign(payload, signing)
    lic = tmp_path / "license.lic"
    pub = tmp_path / "pub.pem"
    clock = tmp_path / "clock.json"
    lic.write_text(json.dumps(doc), encoding="utf-8")
    pub.write_bytes(signing.verify_key.encode())
    clock.write_text(
        json.dumps({"license_id": "lic-roll", "last_seen_at": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOSAIC_REQUIRE_LICENSE", "1")
    monkeypatch.setenv("LICENSE_PATH", str(lic))
    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("LICENSE_CLOCK_PATH", str(clock))
    with pytest.raises(LicenseError, match="时钟回拨"):
        enforce_license_or_raise()


def _base_payload(license_id: str = "lic-m") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "license_id": license_id,
        "customer": "c",
        "product": "spectrum-mosaic",
        "release_version": "0.1.0",
        "starts_at": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _arm_enforce(monkeypatch, tmp_path: Path, signing: SigningKey, payload: dict) -> None:
    lic = tmp_path / "license.lic"
    pub = tmp_path / "pub.pem"
    lic.write_text(json.dumps(_sign(payload, signing)), encoding="utf-8")
    pub.write_bytes(signing.verify_key.encode())
    monkeypatch.setenv("MOSAIC_REQUIRE_LICENSE", "1")
    monkeypatch.setenv("LICENSE_PATH", str(lic))
    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("LICENSE_CLOCK_PATH", str(tmp_path / "clock.json"))
    monkeypatch.setenv("LICENSE_LEASE_PATH", str(tmp_path / "lease.json"))


def test_machine_mismatch(monkeypatch, tmp_path: Path):
    signing = SigningKey.generate()
    host = tmp_path / "machine-id"
    host.write_text("this-host\n", encoding="utf-8")
    payload = _base_payload()
    payload["machine_id"] = "ab" * 32
    _arm_enforce(monkeypatch, tmp_path, signing, payload)
    monkeypatch.setenv("HOST_MACHINE_ID_PATH", str(host))
    with pytest.raises(LicenseError, match="不匹配"):
        enforce_license_or_raise()


def test_bound_license_stores_lease(monkeypatch, tmp_path: Path):
    signing = SigningKey.generate()
    host = tmp_path / "machine-id"
    host.write_text("this-host\n", encoding="utf-8")
    fp = machine_fingerprint("this-host")
    payload = _base_payload()
    payload["machine_id"] = fp
    _arm_enforce(monkeypatch, tmp_path, signing, payload)
    monkeypatch.setenv("HOST_MACHINE_ID_PATH", str(host))
    monkeypatch.setenv("LICENSE_SERVER_URL", "http://license.example")
    now = datetime.now(timezone.utc)
    lease = _sign(
        {
            "license_id": "lic-m",
            "machine_id": fp,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        signing,
    )

    def _ok(*_a, **_k):
        return lease

    monkeypatch.setattr("ms_mosaic.license_check.fetch_lease", _ok)
    enforce_license_or_raise()
    saved = json.loads((tmp_path / "lease.json").read_text(encoding="utf-8"))
    assert saved["payload"]["machine_id"] == fp


def test_offline_cached_lease_accepted(monkeypatch, tmp_path: Path):
    signing = SigningKey.generate()
    host = tmp_path / "machine-id"
    host.write_text("this-host\n", encoding="utf-8")
    fp = machine_fingerprint("this-host")
    payload = _base_payload()
    payload["machine_id"] = fp
    _arm_enforce(monkeypatch, tmp_path, signing, payload)
    monkeypatch.setenv("HOST_MACHINE_ID_PATH", str(host))
    monkeypatch.setenv("LICENSE_SERVER_URL", "http://license.example")
    now = datetime.now(timezone.utc)
    lease = _sign(
        {
            "license_id": "lic-m",
            "machine_id": fp,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        signing,
    )
    (tmp_path / "lease.json").write_text(json.dumps(lease), encoding="utf-8")

    def _down(*_a, **_k):
        raise LeaseNetworkError("down")

    monkeypatch.setattr("ms_mosaic.license_check.fetch_lease", _down)
    enforce_license_or_raise()


def test_server_reject_drops_lease(monkeypatch, tmp_path: Path):
    signing = SigningKey.generate()
    host = tmp_path / "machine-id"
    host.write_text("this-host\n", encoding="utf-8")
    fp = machine_fingerprint("this-host")
    payload = _base_payload()
    payload["machine_id"] = fp
    _arm_enforce(monkeypatch, tmp_path, signing, payload)
    monkeypatch.setenv("HOST_MACHINE_ID_PATH", str(host))
    monkeypatch.setenv("LICENSE_SERVER_URL", "http://license.example")
    lease_path = tmp_path / "lease.json"
    lease_path.write_text("{}", encoding="utf-8")

    def _no(*_a, **_k):
        raise LeaseRejected("许可已停用")

    monkeypatch.setattr("ms_mosaic.license_check.fetch_lease", _no)
    with pytest.raises(LeaseRejected):
        enforce_license_or_raise()
    assert not lease_path.exists()
