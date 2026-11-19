from datetime import datetime, timezone
from zipfile import ZipFile

from app.models import Customer, License, Release
from app.services.packager import build_delivery_zip


def test_delivery_zip_includes_gpu_overlay(settings, tmp_path):
    lic_dir = tmp_path / "lic"
    lic_dir.mkdir()
    lic_file = lic_dir / "demo.lic"
    lic_file.write_text("{}", encoding="utf-8")
    now = datetime.now(timezone.utc)
    customer = Customer(id=1, name="单测客户", status="trial")
    release = Release(id=1, version="0.0.0-test", arch="amd64", status="success")
    license_row = License(
        id=1,
        license_id="demo-lic",
        customer_id=1,
        release_id=1,
        starts_at=now,
        expires_at=now,
        issued_at=now,
        file_path=str(lic_file),
        status="active",
    )
    zip_path, _digest = build_delivery_zip(
        settings=settings,
        customer=customer,
        release=release,
        license_row=license_row,
        include_image_tar=False,
        db=None,
    )
    with ZipFile(zip_path) as zf:
        names = zf.namelist()
        gpu_yml = zf.read("docker-compose.gpu.yml").decode("utf-8")
        start_sh = zf.read("start.sh").decode("utf-8")
        start_ps1 = zf.read("start.ps1").decode("utf-8")
    assert "docker-compose.gpu.yml" in names
    assert "driver: nvidia" in gpu_yml
    assert "capabilities: [gpu]" in gpu_yml
    assert "build:" not in gpu_yml
    assert "docker-compose.yml" in names
    assert "start.ps1" in names
    assert "docker-compose.gpu.yml" in start_sh
    assert "nvidia-smi" in start_sh
    assert "nvidia-smi" in start_ps1
    assert "docker-compose.gpu.yml" in start_ps1


def _delivery_fixtures(tmp_path, *, machine_id: str = ""):
    lic_dir = tmp_path / "lic"
    lic_dir.mkdir()
    lic_file = lic_dir / "demo.lic"
    lic_file.write_text("{}", encoding="utf-8")
    now = datetime.now(timezone.utc)
    customer = Customer(id=1, name="单测客户", status="trial")
    release = Release(id=1, version="0.0.0-test", arch="amd64", status="success")
    license_row = License(
        id=1,
        license_id="demo-lic",
        customer_id=1,
        release_id=1,
        starts_at=now,
        expires_at=now,
        issued_at=now,
        file_path=str(lic_file),
        status="active",
        machine_id=machine_id,
    )
    return customer, release, license_row


def _patch_hours(monkeypatch, url: str) -> None:
    monkeypatch.setattr("app.services.packager.get_clock_tolerance_hours", lambda db, customer=None: 1.0)
    monkeypatch.setattr("app.services.packager.get_lease_interval_hours", lambda db, customer=None: 6.0)
    monkeypatch.setattr("app.services.packager.get_license_server_url", lambda db: url)


def test_delivery_writes_license_server_url(settings, tmp_path, monkeypatch):
    _patch_hours(monkeypatch, "http://123.56.26.229:8180")
    customer, release, license_row = _delivery_fixtures(tmp_path, machine_id="ab" * 32)
    zip_path, _digest = build_delivery_zip(
        settings=settings,
        customer=customer,
        release=release,
        license_row=license_row,
        include_image_tar=False,
        db=object(),
    )
    with ZipFile(zip_path) as zf:
        env = zf.read(".env.example").decode("utf-8")
        compose = zf.read("docker-compose.yml").decode("utf-8")
    assert "LICENSE_SERVER_URL=http://123.56.26.229:8180" in env
    assert "LICENSE_SERVER_URL: ${LICENSE_SERVER_URL:-http://123.56.26.229:8180}" in compose


def test_bound_delivery_requires_license_server_url(settings, tmp_path, monkeypatch):
    _patch_hours(monkeypatch, "")
    customer, release, license_row = _delivery_fixtures(tmp_path, machine_id="ab" * 32)
    try:
        build_delivery_zip(
            settings=settings,
            customer=customer,
            release=release,
            license_row=license_row,
            include_image_tar=False,
            db=object(),
        )
    except ValueError as exc:
        assert "许可服务器地址" in str(exc)
    else:
        raise AssertionError("绑定许可在地址为空时仍打出了包")
