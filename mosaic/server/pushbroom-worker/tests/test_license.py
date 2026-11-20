"""客户镜像（MOSAIC_REQUIRE_LICENSE=1）下，流水线入口自己验许可。"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nacl.signing import SigningKey
from synth import SENSOR_PARAMS

from pb_mosaic.__main__ import main
from pb_mosaic.license_check import PRODUCT_NAME, _canonical_payload
from pb_mosaic.pipeline import PipelineError, calibrate_flight, run

FMT = "%Y-%m-%dT%H:%M:%SZ"


def _license(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, expires_in: timedelta) -> None:
    key = SigningKey.generate()
    now = datetime.now(timezone.utc)
    payload = {
        "license_id": "pb-test",
        "customer": "单测",
        "product": PRODUCT_NAME,
        "release_version": "0.0.0",
        "starts_at": (now - timedelta(days=1)).strftime(FMT),
        "expires_at": (now + expires_in).strftime(FMT),
        "issued_at": now.strftime(FMT),
    }
    sig = key.sign(_canonical_payload(payload)).signature
    lic = tmp_path / "license.lic"
    lic.write_text(json.dumps({"payload": payload, "signature": base64.b64encode(sig).decode()}), encoding="utf-8")
    pub = tmp_path / "license_public.pem"
    pub.write_bytes(bytes(key.verify_key))
    monkeypatch.setenv("LICENSE_PATH", str(lic))
    monkeypatch.setenv("LICENSE_PUBLIC_KEY_PATH", str(pub))


@pytest.fixture()
def require_license(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOSAIC_REQUIRE_LICENSE", "1")
    monkeypatch.setenv("LICENSE_PATH", str(tmp_path / "missing.lic"))
    monkeypatch.setenv("LICENSE_CLOCK_PATH", str(tmp_path / "clock.json"))


def test_run_refuses_without_license(require_license, synth_flight, tmp_path: Path) -> None:
    out = tmp_path / "out"
    with pytest.raises(PipelineError, match="许可"):
        run(synth_flight.root, out, {"bands": [1]})
    assert not out.exists()


def test_calibrate_refuses_without_license(require_license, synth_flight) -> None:
    with pytest.raises(PipelineError, match="许可"):
        calibrate_flight(synth_flight.root)


def test_cli_run_refuses_without_license(require_license, synth_flight, tmp_path: Path, capsys) -> None:
    code = main(["run", "--input", str(synth_flight.root), "--output", str(tmp_path / "out"), "--bands", "1"])
    assert code == 1
    assert "许可" in capsys.readouterr().err


def test_expired_license_refused(require_license, synth_flight, tmp_path: Path, monkeypatch) -> None:
    _license(tmp_path, monkeypatch, expires_in=timedelta(days=-1))
    with pytest.raises(PipelineError, match="过期"):
        calibrate_flight(synth_flight.root)


def test_valid_license_runs(require_license, synth_flight, tmp_path: Path, monkeypatch) -> None:
    _license(tmp_path, monkeypatch, expires_in=timedelta(days=30))
    result = run(synth_flight.root, tmp_path / "out", {**SENSOR_PARAMS, "bands": [1], "align": False})
    assert (tmp_path / "clock.json").is_file()
    assert result.products
