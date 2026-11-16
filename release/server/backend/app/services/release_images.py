from __future__ import annotations

# 与 build_runner / load-images.sh / customer-compose 一致
DEP_IMAGE_REFS: tuple[str, ...] = (
    "postgres:16-alpine",
    "rabbitmq:3.13-management-alpine",
)


def build_image_manifest(
    *,
    worker: str,
    backend: str,
    frontend: str,
    deps: tuple[str, ...] = DEP_IMAGE_REFS,
) -> str:
    """全量离线镜像清单（多行，供 releases.image_name 持久化与列表展示）。"""
    return "\n".join([worker, backend, frontend, *deps])


def resolve_worker_image(
    image_name: str,
    *,
    version: str,
    arch: str,
    worker_repo: str = "spectrum-mosaic-worker",
) -> str:
    """从清单或旧版单行 image_name 解析 worker 镜像，供 compose {{IMAGE_NAME}}。"""
    lines = [ln.strip() for ln in (image_name or "").splitlines() if ln.strip()]
    for ln in lines:
        repo = ln.rsplit(":", 1)[0].split("/")[-1]
        if repo == worker_repo:
            return ln
    return f"{worker_repo}:{version}-{arch}"


def expand_legacy_image_name(
    image_name: str,
    *,
    version: str,
    arch: str,
    worker_repo: str = "spectrum-mosaic-worker",
    backend_repo: str = "spectrum-mosaic-backend",
    frontend_repo: str = "spectrum-mosaic-frontend",
) -> str:
    """旧数据仅存 worker 单行时，列表展示展开为全量清单（不写库）。"""
    text = (image_name or "").strip()
    if not text or "\n" in text:
        return image_name or ""
    worker = text
    repo = text.rsplit(":", 1)[0].split("/")[-1]
    if repo != worker_repo:
        worker = f"{worker_repo}:{version}-{arch}"
    return build_image_manifest(
        worker=worker,
        backend=f"{backend_repo}:{version}-{arch}",
        frontend=f"{frontend_repo}:{version}-{arch}",
    )
