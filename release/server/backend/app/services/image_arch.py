"""Inspect architecture of a `docker save` tarball without loading it."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path


def tar_image_architectures(tar_path: Path) -> list[str]:
    """Return Architecture values from configs inside a docker-save archive."""
    arches: list[str] = []
    with tarfile.open(tar_path, "r") as tf:
        try:
            mf = tf.extractfile("manifest.json")
        except KeyError as exc:
            raise ValueError(f"{tar_path.name}: 不是有效的 docker save 包（缺 manifest.json）") from exc
        if mf is None:
            raise ValueError(f"{tar_path.name}: 无法读取 manifest.json")
        manifest = json.load(mf)
        if not isinstance(manifest, list) or not manifest:
            raise ValueError(f"{tar_path.name}: manifest.json 为空")
        for entry in manifest:
            config_name = entry.get("Config")
            if not config_name:
                continue
            cfg_f = tf.extractfile(config_name)
            if cfg_f is None:
                continue
            cfg = json.load(cfg_f)
            arch = (cfg.get("architecture") or cfg.get("Architecture") or "").strip()
            if arch:
                arches.append(arch)
    if not arches:
        raise ValueError(f"{tar_path.name}: 未能从镜像包解析出 architecture")
    return arches


def assert_tar_arch(tar_path: Path, want: str) -> str:
    """Ensure every config in the tarball matches want (e.g. amd64). Return the arch."""
    arches = tar_image_architectures(tar_path)
    bad = sorted({a for a in arches if a != want})
    if bad:
        raise ValueError(
            f"{tar_path.name}: 期望架构 {want}，但包内含 {', '.join(arches)}（错误: {', '.join(bad)}）"
        )
    return want
