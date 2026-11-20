"""构建产物目录中的离线镜像 tar（与 packager / load-images 一致）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# name -> 磁盘文件名
ARTIFACT_FILES: dict[str, str] = {
    "worker": "worker-image.tar",
    "pushbroom_worker": "pushbroom-worker-image.tar",
    "backend": "backend-image.tar",
    "frontend": "frontend-image.tar",
    "postgres": "postgres-image.tar",
    "rabbitmq": "rabbitmq-image.tar",
}

ARTIFACT_ORDER: tuple[str, ...] = (
    "worker",
    "pushbroom_worker",
    "backend",
    "frontend",
    "postgres",
    "rabbitmq",
)

ARTIFACT_LABELS: dict[str, str] = {
    "worker": "Worker",
    "pushbroom_worker": "推扫 Worker",
    "backend": "Backend",
    "frontend": "Frontend",
    "postgres": "Postgres",
    "rabbitmq": "RabbitMQ",
}


def artifacts_dir(row: Any) -> Path | None:
    if not getattr(row, "artifact_path", None):
        return None
    path = Path(row.artifact_path)
    if path.is_file():
        return path.parent
    if path.is_dir():
        return path
    parent = path.parent
    return parent if parent.is_dir() else None


def resolve_artifact_file(row: Any, name: str) -> Path | None:
    key = (name or "").strip().lower()
    filename = ARTIFACT_FILES.get(key)
    if not filename:
        return None
    root = artifacts_dir(row)
    if root is None:
        return None
    path = root / filename
    return path if path.is_file() else None


def list_artifacts(row: Any) -> list[dict]:
    root = artifacts_dir(row)
    items: list[dict] = []
    for name in ARTIFACT_ORDER:
        filename = ARTIFACT_FILES[name]
        path = (root / filename) if root is not None else None
        available = bool(path and path.is_file())
        items.append(
            {
                "name": name,
                "label": ARTIFACT_LABELS[name],
                "filename": filename,
                "size_bytes": path.stat().st_size if available and path is not None else 0,
                "available": available,
            }
        )
    return items


def download_filename(row: Any, name: str) -> str:
    arch = (getattr(row, "arch", None) or "arch").strip() or "arch"
    version = (getattr(row, "version", None) or "unknown").strip() or "unknown"
    return f"{name}-{version}-{arch}.tar"
