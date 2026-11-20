from __future__ import annotations

# 与 build_runner / load-images.sh / customer-compose 一致
DEP_IMAGE_REFS: tuple[str, ...] = (
    "postgres:16-alpine",
    "rabbitmq:3.13-management-alpine",
)


PUSHBROOM_WORKER_REPO = "spectrum-mosaic-pushbroom-worker"


def build_image_manifest(
    *,
    worker: str,
    backend: str,
    frontend: str,
    deps: tuple[str, ...] = DEP_IMAGE_REFS,
    pushbroom_worker: str | None = None,
) -> str:
    """全量离线镜像清单（多行，供 releases.image_name 持久化与列表展示）。

    推扫 worker 是后加的：老版本清单里没有这一行，打包时据此关闭推扫。
    """
    images = [worker, *([pushbroom_worker] if pushbroom_worker else []), backend, frontend, *deps]
    return "\n".join(images)


def _find_repo(image_name: str, repo: str) -> str | None:
    for ln in (image_name or "").splitlines():
        ln = ln.strip()
        if ln and ln.rsplit(":", 1)[0].split("/")[-1] == repo:
            return ln
    return None


def resolve_worker_image(
    image_name: str,
    *,
    version: str,
    arch: str,
    worker_repo: str = "spectrum-mosaic-worker",
) -> str:
    """从清单或旧版单行 image_name 解析 worker 镜像，供 compose {{IMAGE_NAME}}。"""
    return _find_repo(image_name, worker_repo) or f"{worker_repo}:{version}-{arch}"


def resolve_pushbroom_image(
    image_name: str,
    *,
    pushbroom_repo: str = PUSHBROOM_WORKER_REPO,
) -> str | None:
    """清单里的推扫 worker 镜像；该版本没有构建推扫时返回 None。"""
    return _find_repo(image_name, pushbroom_repo)


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
