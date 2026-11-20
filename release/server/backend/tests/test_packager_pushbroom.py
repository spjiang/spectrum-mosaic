import io
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.models import Customer, License, Release
from app.services.packager import PUSHBROOM_TAR, _IMAGE_TARS, build_delivery_zip
from app.services.release_images import build_image_manifest

ARCH = "arm64"
VERSION = "0.2.0-test"
PUSHBROOM_REF = f"spectrum-mosaic-pushbroom-worker:{VERSION}-{ARCH}"
# 模板占位符；脚本里 docker 的 {{.Repository}} 格式串不算
PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")


def _manifest(with_pushbroom: bool) -> str:
    return build_image_manifest(
        worker=f"spectrum-mosaic-worker:{VERSION}-{ARCH}",
        pushbroom_worker=PUSHBROOM_REF if with_pushbroom else None,
        backend=f"spectrum-mosaic-backend:{VERSION}-{ARCH}",
        frontend=f"spectrum-mosaic-frontend:{VERSION}-{ARCH}",
    )


def _fake_docker_save(path: Path) -> None:
    config = {"architecture": ARCH, "os": "linux", "rootfs": {"type": "layers", "diff_ids": []}}
    manifest = [{"Config": "c.json", "RepoTags": [path.stem], "Layers": []}]
    with tarfile.open(path, "w") as tf:
        for name, doc in (("c.json", config), ("manifest.json", manifest)):
            raw = json.dumps(doc).encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))


def _fixtures(tmp_path: Path, *, with_pushbroom: bool, tars: tuple[str, ...] = ()):
    lic_file = tmp_path / "demo.lic"
    lic_file.write_text("{}", encoding="utf-8")
    images = tmp_path / "images"
    images.mkdir()
    for name in tars:
        _fake_docker_save(images / name)
    now = datetime.now(timezone.utc)
    customer = Customer(id=1, name="单测客户", status="trial")
    release = Release(
        id=1,
        version=VERSION,
        arch=ARCH,
        status="success",
        image_name=_manifest(with_pushbroom),
        artifact_path=str(images / "worker-image.tar"),
    )
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
    return customer, release, license_row


def _build(settings, fixtures, *, include_image_tar: bool) -> Path:
    customer, release, license_row = fixtures
    zip_path, _ = build_delivery_zip(
        settings=settings,
        customer=customer,
        release=release,
        license_row=license_row,
        include_image_tar=include_image_tar,
        db=None,
    )
    return zip_path


def _read(zip_path: Path) -> dict[str, str]:
    with ZipFile(zip_path) as zf:
        return {
            n: zf.read(n).decode("utf-8")
            for n in zf.namelist()
            if not n.endswith(".tar") and not n.endswith(".arch")
        }


def test_pushbroom_release_enables_service(settings, tmp_path):
    files = _read(_build(settings, _fixtures(tmp_path, with_pushbroom=True), include_image_tar=False))
    compose, env = files["docker-compose.yml"], files[".env.example"]
    assert f"image: {PUSHBROOM_REF}" in compose
    assert 'profiles: ["pushbroom"]' in compose
    assert "PUSHBROOM_ENABLED: ${PUSHBROOM_ENABLED:-false}" in compose
    assert "PB_SENSOR_ALONG_OFFSET_M: ${PB_SENSOR_ALONG_OFFSET_M:-}" in compose
    assert "LICENSE_LEASE_PATH: /data/.license/pushbroom-lease.json" in compose
    assert "./licenses/demo-lic.lic:/license/license.lic:ro" in compose
    assert "PUSHBROOM_ENABLED=true" in env.splitlines()
    assert "COMPOSE_PROFILES=pushbroom" in env.splitlines()
    for name, text in files.items():
        assert not PLACEHOLDER.search(text), f"{name} 留有未替换的占位符"


def test_old_release_keeps_pushbroom_off(settings, tmp_path):
    files = _read(_build(settings, _fixtures(tmp_path, with_pushbroom=False), include_image_tar=False))
    env = files[".env.example"].splitlines()
    assert "PUSHBROOM_ENABLED=false" in env
    assert "COMPOSE_PROFILES=" in env
    assert f"image: {PUSHBROOM_REF}" in files["docker-compose.yml"]
    for name, text in files.items():
        assert not PLACEHOLDER.search(text), f"{name} 留有未替换的占位符"


def test_pushbroom_release_requires_its_image_tar(settings, tmp_path):
    fixtures = _fixtures(tmp_path, with_pushbroom=True, tars=_IMAGE_TARS)
    with pytest.raises(FileNotFoundError, match=PUSHBROOM_TAR):
        _build(settings, fixtures, include_image_tar=True)


def test_pushbroom_image_tar_packed(settings, tmp_path):
    fixtures = _fixtures(tmp_path, with_pushbroom=True, tars=(*_IMAGE_TARS, PUSHBROOM_TAR))
    with ZipFile(_build(settings, fixtures, include_image_tar=True)) as zf:
        names = set(zf.namelist())
    assert f"images/{PUSHBROOM_TAR}" in names
    assert "images/load-images.sh" in names


def test_old_release_packs_without_pushbroom_tar(settings, tmp_path):
    fixtures = _fixtures(tmp_path, with_pushbroom=False, tars=_IMAGE_TARS)
    with ZipFile(_build(settings, fixtures, include_image_tar=True)) as zf:
        names = set(zf.namelist())
    assert f"images/{PUSHBROOM_TAR}" not in names
    assert "images/worker-image.tar" in names
