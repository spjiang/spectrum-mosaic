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
