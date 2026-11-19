from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Customer, License, Release
from app.services.image_arch import assert_tar_arch
from app.services.release_images import resolve_worker_image
from app.services.system_settings import (
    format_hours,
    get_clock_tolerance_hours,
    get_lease_interval_hours,
    get_license_server_url,
)

# 与 build_runner 产出、load-images.sh 一致
_IMAGE_TARS = (
    "worker-image.tar",
    "backend-image.tar",
    "frontend-image.tar",
    "postgres-image.tar",
    "rabbitmq-image.tar",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _workspace() -> Path:
    ws = Path("/workspace")
    if ws.exists():
        return ws
    return Path(__file__).resolve().parents[5]


def build_delivery_zip(
    *,
    settings: Settings,
    customer: Customer,
    release: Release,
    license_row: License,
    include_image_tar: bool,
    db: Session | None = None,
) -> tuple[Path, str]:
    templates = _workspace() / "release" / "build" / "templates"
    out_dir = Path(settings.artifacts_dir) / "deliveries"
    out_dir.mkdir(parents=True, exist_ok=True)
    arch = (release.arch or "arm64").strip().lower() or "arm64"
    zip_path = out_dir / f"{customer.name}_{release.version}_{arch}_{license_row.license_id}.zip".replace("/", "_")

    staging = out_dir / f".stage_{license_row.license_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    (staging / "licenses").mkdir()
    lic_src = Path(license_row.file_path)
    if not lic_src.exists():
        raise FileNotFoundError("许可文件不存在")
    shutil.copy2(lic_src, staging / "licenses" / f"{license_row.license_id}.lic")

    tolerance_hours = "1"
    interval_hours = "6"
    license_server_url = ""
    if db is not None:
        tolerance_hours = format_hours(get_clock_tolerance_hours(db, customer=customer))
        interval_hours = format_hours(get_lease_interval_hours(db, customer=customer))
        license_server_url = get_license_server_url(db)
    if (license_row.machine_id or "").strip() and not license_server_url:
        raise ValueError("绑定许可打包须先在全局配置填写许可服务器地址")

    compose_src = templates / "customer-compose.yml"
    gpu_compose_src = templates / "customer-compose.gpu.yml"
    readme_src = templates / "customer-README.md"
    env_src = templates / "customer.env.example"
    if not gpu_compose_src.exists():
        raise FileNotFoundError(f"缺少 {gpu_compose_src}")
    shutil.copy2(gpu_compose_src, staging / "docker-compose.gpu.yml")
    for src, name in (
        (compose_src, "docker-compose.yml"),
        (readme_src, "README.md"),
        (env_src, ".env.example"),
    ):
        text = src.read_text(encoding="utf-8")
        text = (
            text.replace("{{VERSION}}", release.version)
            .replace("{{ARCH}}", arch)
            .replace(
                "{{IMAGE_NAME}}",
                resolve_worker_image(
                    release.image_name or "",
                    version=release.version,
                    arch=arch,
                ),
            )
            .replace("{{LICENSE_FILE}}", f"{license_row.license_id}.lic")
            .replace("{{CUSTOMER}}", customer.name)
            .replace("{{LICENSE_CLOCK_TOLERANCE_HOURS}}", tolerance_hours)
            .replace("{{LICENSE_LEASE_CHECK_INTERVAL_HOURS}}", interval_hours)
            .replace("{{LICENSE_SERVER_URL}}", license_server_url)
        )
        (staging / name).write_text(text, encoding="utf-8")

    for name in ("start.sh", "start.ps1", "start.cmd", "uninstall.sh", "uninstall.ps1", "uninstall.cmd"):
        src = templates / name
        if not src.exists():
            raise FileNotFoundError(f"缺少 {src}")
        dst = staging / name
        shutil.copy2(src, dst)
        if name.endswith(".sh"):
            dst.chmod(0o755)

    docs_dst = staging / "docs"
    docs_dst.mkdir()
    manual_src = _workspace() / "docs" / "操作文档"
    for name in (
        "拼图服务私有部署操作手册.md",
        "拼图服务WEB操作手册.md",
    ):
        src = manual_src / name
        if not src.exists():
            raise FileNotFoundError(f"缺少拼图服务操作手册: {src}")
        shutil.copy2(src, docs_dst / name)

    if include_image_tar:
        if not release.artifact_path:
            raise FileNotFoundError("镜像产物路径为空，请先成功构建版本")
        release_images = Path(release.artifact_path).parent
        images = staging / "images"
        images.mkdir()
        missing: list[str] = []
        arch_errors: list[str] = []
        for name in _IMAGE_TARS:
            src = release_images / name
            if not src.exists():
                missing.append(name)
                continue
            try:
                assert_tar_arch(src, arch)
            except ValueError as exc:
                arch_errors.append(str(exc))
                continue
            shutil.copy2(src, images / name)
            sidecar = Path(str(src) + ".arch")
            if sidecar.exists():
                shutil.copy2(sidecar, images / (name + ".arch"))
        if missing:
            raise FileNotFoundError(
                "缺少全量离线镜像（请重新构建该版本以生成完整栈）: " + ", ".join(missing)
            )
        if arch_errors:
            raise ValueError(
                "交付镜像架构与版本 arch 不一致（请重新构建该架构）:\n" + "\n".join(arch_errors)
            )
        load_sh = templates / "load-images.sh"
        if not load_sh.exists():
            raise FileNotFoundError(f"缺少 {load_sh}")
        dst_sh = images / "load-images.sh"
        shutil.copy2(load_sh, dst_sh)
        dst_sh.chmod(0o755)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in staging.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(staging).as_posix())

    shutil.rmtree(staging, ignore_errors=True)
    return zip_path, _sha256_file(zip_path)
