import io
import json
import tarfile
from pathlib import Path

import pytest

from app.services.image_arch import assert_tar_arch, tar_image_architectures


def _fake_docker_save(path: Path, *, architecture: str, repo_tags: list[str] | None = None) -> None:
    config = {
        "architecture": architecture,
        "os": "linux",
        "rootfs": {"type": "layers", "diff_ids": []},
        "config": {},
    }
    config_name = "abcd1234.json"
    manifest = [
        {
            "Config": config_name,
            "RepoTags": repo_tags or ["postgres:16-alpine"],
            "Layers": [],
        }
    ]
    with tarfile.open(path, "w") as tf:
        raw = json.dumps(config).encode()
        info = tarfile.TarInfo(config_name)
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
        mraw = json.dumps(manifest).encode()
        minfo = tarfile.TarInfo("manifest.json")
        minfo.size = len(mraw)
        tf.addfile(minfo, io.BytesIO(mraw))


def test_tar_image_architectures_reads_amd64(tmp_path: Path):
    p = tmp_path / "postgres-image.tar"
    _fake_docker_save(p, architecture="amd64")
    assert tar_image_architectures(p) == ["amd64"]
    assert assert_tar_arch(p, "amd64") == "amd64"


def test_assert_tar_arch_rejects_mismatch(tmp_path: Path):
    p = tmp_path / "postgres-image.tar"
    _fake_docker_save(p, architecture="arm64")
    with pytest.raises(ValueError, match="期望架构 amd64"):
        assert_tar_arch(p, "amd64")
