from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import docker
from docker.errors import DockerException

from app.config import get_settings
from app.db import SessionLocal
from app.models import Release
from app.services.image_arch import assert_tar_arch
from app.services.license_crypto import PUBLIC_NAME
from app.services.release_images import DEP_IMAGE_REFS, build_image_manifest

log = logging.getLogger("release.build")
_lock = threading.Lock()

# 交付包离线全量镜像（与 customer-compose / release_images 一致）
_DEP_IMAGES = tuple((ref, f"{ref.split(':', 1)[0]}-image.tar") for ref in DEP_IMAGE_REFS)
_ALLOWED_ARCH = frozenset({"amd64", "arm64"})
_BUILDX_ENV = {"DOCKER_CLI_PLUGIN_PATH": "/usr/local/lib/docker/cli-plugins"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _image_tag(repo: str, version: str, arch: str) -> str:
    return f"{repo}:{version}-{arch}"


def worker_customer_dockerfile(context: Path, arch: str) -> Path:
    """amd64 客户 worker 带 CUDA/CuPy；arm64 仍用 CPU 镜像。"""
    name = "Dockerfile.worker-customer-gpu" if arch == "amd64" else "Dockerfile.worker-customer"
    return context / "release" / "build" / name


def pushbroom_customer_dockerfile(context: Path) -> Path:
    """推扫 worker 只用 CPU，两种架构同一个 Dockerfile。"""
    return context / "release" / "build" / "Dockerfile.pushbroom-customer"


# 构建产物，最后逐个核对架构；与 packager / load-images.sh 一致
IMAGE_TARS = (
    "worker-image.tar",
    "pushbroom-worker-image.tar",
    "backend-image.tar",
    "frontend-image.tar",
    "postgres-image.tar",
    "rabbitmq-image.tar",
)


def start_build(release_id: int) -> None:
    with _lock:
        threading.Thread(target=_run_build, args=(release_id,), daemon=True).start()


def _workspace() -> Path:
    ws = Path("/workspace")
    if ws.exists():
        return ws
    return Path(__file__).resolve().parents[5]


def _run_logged(
    cmd: list[str],
    logf,
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> None:
    logf.write("$ " + " ".join(cmd) + ("  <<stdin>>" if input_text is not None else "") + "\n")
    logf.flush()
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=run_env,
    )
    assert proc.stdout is not None
    if input_text is not None and proc.stdin is not None:
        proc.stdin.write(input_text)
        proc.stdin.close()
    for line in proc.stdout:
        logf.write(line)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"命令失败 (exit={code}): {' '.join(cmd)}")


def _host_arch() -> str:
    m = subprocess.check_output(["uname", "-m"], text=True).strip()
    if m in {"x86_64", "amd64"}:
        return "amd64"
    if m in {"aarch64", "arm64"}:
        return "arm64"
    return m


def _image_arch(image_ref: str) -> str | None:
    insp = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Architecture}}", image_ref],
        capture_output=True,
        text=True,
    )
    if insp.returncode != 0:
        return None
    return (insp.stdout or "").strip() or None


def _require_image_arch(image_ref: str, want: str, logf) -> None:
    got = _image_arch(image_ref)
    logf.write(f"inspect {image_ref} arch={got!r} want={want!r}\n")
    if got != want:
        raise RuntimeError(f"镜像架构不匹配: {image_ref} got={got} want={want}")


def _save_cli_verified(image_ref: str, tar_path: Path, want_arch: str, logf) -> None:
    """docker save 到 /tmp 再搬到产物目录，并解析 tar 校验 architecture。"""
    _require_image_arch(image_ref, want_arch, logf)
    if tar_path.exists():
        tar_path.unlink()
    tmp = Path("/tmp") / f"mosaic-save-{tar_path.name}"
    if tmp.exists():
        tmp.unlink()
    _run_logged(["docker", "save", "-o", str(tmp), image_ref], logf)
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError(f"docker save 产物无效: {tmp}")
    # 在搬迁前校验，避免错误包进交付目录
    got = assert_tar_arch(tmp, want_arch)
    shutil.move(str(tmp), str(tar_path))
    sidecar = Path(str(tar_path) + ".arch")
    sidecar.write_text(got + "\n", encoding="utf-8")
    logf.write(
        f"SAVED+VERIFIED {image_ref} -> {tar_path.name} arch={got} "
        f"sha256={_sha256_file(tar_path)}\n"
    )


def _save_named_verified(client: docker.DockerClient, image_ref: str, tar_path: Path, want_arch: str, logf) -> str:
    """SDK save（保留 tag）+ tar 架构校验。"""
    img = client.images.get(image_ref)
    tags = img.tags or []
    if image_ref not in tags:
        img.tag(image_ref)
        img = client.images.get(image_ref)
    _require_image_arch(image_ref, want_arch, logf)
    tmp = Path("/tmp") / f"mosaic-save-{tar_path.name}"
    if tmp.exists():
        tmp.unlink()
    with tmp.open("wb") as out:
        for chunk in img.save(named=True):
            out.write(chunk)
    assert_tar_arch(tmp, want_arch)
    if tar_path.exists():
        tar_path.unlink()
    shutil.move(str(tmp), str(tar_path))
    Path(str(tar_path) + ".arch").write_text(want_arch + "\n", encoding="utf-8")
    digest = img.id
    logf.write(
        f"SAVED+VERIFIED {image_ref} -> {tar_path.name} id={digest} arch={want_arch} "
        f"sha256={_sha256_file(tar_path)}\n"
    )
    return digest


def _build_image(
    *,
    context: Path,
    dockerfile: Path,
    tag: str,
    platform: str,
    logf,
) -> None:
    df = str(dockerfile) if dockerfile.is_absolute() else str(context / dockerfile)
    _run_logged(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            platform,
            "--pull",
            "-t",
            tag,
            "-f",
            df,
            "--load",
            str(context),
        ],
        logf,
        env=_BUILDX_ENV,
    )


def _staging_ref(image_ref: str, want: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", image_ref)
    return f"spectrum-offline/{safe}-{want}"


def _force_materialize(image_ref: str, platform: str, dest_tag: str, logf) -> None:
    """始终用 buildx --platform FROM 物化到 dest_tag（不信任本机已有 tag）。"""
    want = platform.split("/", 1)[-1]
    logf.write(f"force materialize {image_ref} -> {dest_tag} ({platform})\n")
    subprocess.run(["docker", "image", "rm", "-f", dest_tag], capture_output=True, text=True)
    _run_logged(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            platform,
            "--pull",
            "-t",
            dest_tag,
            "--load",
            "-",
        ],
        logf,
        env=_BUILDX_ENV,
        input_text=f"FROM {image_ref}\n",
    )
    _require_image_arch(dest_tag, want, logf)


def _pull_and_save_dep(image_ref: str, platform: str, tar_path: Path, logf) -> None:
    """
    依赖镜像离线打包：
    1) buildx 强制物化到 staging tag（避免 Desktop pull --platform 假成功）
    2) 再 tag 成 compose 需要的正式名后 save（tar 内 RepoTags 正确）
    3) 解析 tar 校验 architecture；不过则失败
    4) 若与宿主机架构不同，把正式名物化回本机架构，避免污染发版台本地 compose
    """
    want = platform.split("/", 1)[-1]
    host = _host_arch()
    staging = _staging_ref(image_ref, want)
    _force_materialize(image_ref, platform, staging, logf)

    # 正式名指向目标架构，保证 docker save 写入正确 RepoTags
    subprocess.run(["docker", "image", "rm", "-f", image_ref], capture_output=True, text=True)
    _run_logged(["docker", "tag", staging, image_ref], logf)
    _require_image_arch(image_ref, want, logf)

    _save_cli_verified(image_ref, tar_path, want, logf)

    # 清理 staging
    subprocess.run(["docker", "image", "rm", "-f", staging], capture_output=True, text=True)

    if host != want:
        logf.write(f"restore host arch linux/{host} for local tag {image_ref}\n")
        host_staging = _staging_ref(image_ref, host)
        _force_materialize(image_ref, f"linux/{host}", host_staging, logf)
        subprocess.run(["docker", "image", "rm", "-f", image_ref], capture_output=True, text=True)
        _run_logged(["docker", "tag", host_staging, image_ref], logf)
        subprocess.run(["docker", "image", "rm", "-f", host_staging], capture_output=True, text=True)
        _require_image_arch(image_ref, host, logf)


def _run_build(release_id: int) -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        rel = db.get(Release, release_id)
        if rel is None:
            return
        arch = (rel.arch or "").strip().lower()
        if arch not in _ALLOWED_ARCH:
            raise RuntimeError(f"非法 arch: {rel.arch!r}（仅支持 amd64/arm64）")
        platform = f"linux/{arch}"

        out_dir = Path(settings.artifacts_dir) / "releases" / str(release_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / "build.log"
        worker_tag = _image_tag(settings.worker_image_repo, rel.version, arch)
        pushbroom_tag = _image_tag(settings.pushbroom_worker_image_repo, rel.version, arch)
        backend_tag = _image_tag(settings.backend_image_repo, rel.version, arch)
        frontend_tag = _image_tag(settings.frontend_image_repo, rel.version, arch)
        image_manifest = build_image_manifest(
            worker=worker_tag,
            pushbroom_worker=pushbroom_tag,
            backend=backend_tag,
            frontend=frontend_tag,
        )
        rel.status = "running"
        rel.error_message = ""
        rel.log_path = str(log_path)
        rel.image_name = image_manifest
        db.commit()

        pub = Path(settings.keys_dir) / PUBLIC_NAME
        if not pub.exists():
            raise RuntimeError("公钥不存在，无法打进客户镜像")

        context = _workspace()
        worker_df = worker_customer_dockerfile(context, arch)
        pushbroom_df = pushbroom_customer_dockerfile(context)
        backend_df = context / "release" / "build" / "Dockerfile.backend-customer"
        frontend_ctx = context / "mosaic" / "server" / "frontend"
        frontend_df = frontend_ctx / "Dockerfile"
        for p in (worker_df, pushbroom_df, backend_df, frontend_df):
            if not p.exists():
                raise RuntimeError(f"找不到 Dockerfile: {p}")

        stage_key = context / "release" / "build" / ".embed" / "license_public.pem"
        stage_key.parent.mkdir(parents=True, exist_ok=True)
        stage_key.write_bytes(pub.read_bytes())

        client = docker.from_env()
        worker_tar = out_dir / "worker-image.tar"
        pushbroom_tar = out_dir / "pushbroom-worker-image.tar"
        backend_tar = out_dir / "backend-image.tar"
        frontend_tar = out_dir / "frontend-image.tar"

        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(f"platform={platform}\n")
            logf.write(f"worker dockerfile={worker_df}\n")
            logf.write(f"pushbroom worker dockerfile={pushbroom_df}\n")
            logf.write("=== full offline stack build ===\n")

            try:
                client.ping()
            except DockerException as exc:
                raise RuntimeError(f"无法连接 Docker（请确认挂载了 docker.sock）: {exc}") from exc

            _build_image(
                context=context,
                dockerfile=worker_df,
                tag=worker_tag,
                platform=platform,
                logf=logf,
            )
            worker_digest = _save_named_verified(client, worker_tag, worker_tar, arch, logf)

            _build_image(
                context=context,
                dockerfile=pushbroom_df,
                tag=pushbroom_tag,
                platform=platform,
                logf=logf,
            )
            _save_named_verified(client, pushbroom_tag, pushbroom_tar, arch, logf)

            _build_image(
                context=context,
                dockerfile=backend_df,
                tag=backend_tag,
                platform=platform,
                logf=logf,
            )
            _save_named_verified(client, backend_tag, backend_tar, arch, logf)

            _build_image(
                context=frontend_ctx,
                dockerfile=frontend_df,
                tag=frontend_tag,
                platform=platform,
                logf=logf,
            )
            _save_named_verified(client, frontend_tag, frontend_tar, arch, logf)

            for ref, tar_name in _DEP_IMAGES:
                _pull_and_save_dep(ref, platform, out_dir / tar_name, logf)

            # 最终再扫一遍全部 tar，防止遗漏
            for name in IMAGE_TARS:
                assert_tar_arch(out_dir / name, arch)
                logf.write(f"FINAL_OK {name} arch={arch}\n")

            manifest = out_dir / "images.manifest"
            lines = [
                f"arch={arch}",
                f"platform={platform}",
                f"worker={worker_tag}",
                f"pushbroom_worker={pushbroom_tag}",
                f"backend={backend_tag}",
                f"frontend={frontend_tag}",
                *[f"dep={ref}" for ref, _ in _DEP_IMAGES],
            ]
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logf.write("MANIFEST:\n" + manifest.read_text(encoding="utf-8"))
            logf.write("BUILD_OK\n")

        rel = db.get(Release, release_id)
        assert rel is not None
        rel.status = "success"
        rel.digest = worker_digest
        rel.artifact_path = str(worker_tar)
        rel.error_message = ""
        db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("build failed release_id=%s", release_id)
        rel = db.get(Release, release_id)
        if rel is not None:
            rel.status = "failed"
            rel.error_message = str(exc)[:2000]
            db.commit()
    finally:
        db.close()
