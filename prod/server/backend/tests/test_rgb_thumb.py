"""列表缩略图不能把正射还在写时的全灰图一直缓存下去。"""

import io
import os
from types import SimpleNamespace

import numpy as np
from PIL import Image

from app.services import seam_map


def _job(tmp_path, status="running"):
    products = tmp_path / "拼图结果"
    products.mkdir()
    ortho = products / seam_map.ORTHO_NAME
    ortho.write_bytes(b"ortho")
    job = SimpleNamespace(
        products_dir=str(products),
        output_dir=str(tmp_path),
        status=status,
    )
    return job, products, ortho


def _jpeg(color, size=(8, 8)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _color(data: bytes):
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB").getpixel((0, 0))


def test_stale_gray_thumb_is_rebuilt_after_ortho_finishes(tmp_path, monkeypatch):
    job, products, ortho = _job(tmp_path, status="succeeded")
    thumb = products / seam_map.THUMB_NAME
    gray = _jpeg((236, 236, 236))
    thumb.write_bytes(gray)
    os.utime(thumb, (ortho.stat().st_mtime - 30, ortho.stat().st_mtime - 30))
    calls = {"n": 0}

    def overview(path, max_side=480):
        calls["n"] += 1
        rgb = np.zeros((6, 8, 3), np.uint8)
        rgb[:] = (12, 80, 40)
        return rgb, 6, 8, 3, "uint8"

    monkeypatch.setattr(seam_map, "_load_tiff_overview", overview)
    data = seam_map.rgb_thumb_jpeg(job)
    assert data != gray
    assert _color(data) != (236, 236, 236)
    assert calls["n"] == 1
    assert seam_map.rgb_thumb_jpeg(job) == data
    assert calls["n"] == 1


def test_finished_thumb_is_reused(tmp_path, monkeypatch):
    job, products, ortho = _job(tmp_path, status="succeeded")
    thumb = products / seam_map.THUMB_NAME
    stored = _jpeg((12, 80, 40))
    thumb.write_bytes(stored)
    os.utime(ortho, (thumb.stat().st_mtime - 30, thumb.stat().st_mtime - 30))

    def overview(path, max_side=480):
        raise AssertionError("已有新缩略图不应再读正射")

    monkeypatch.setattr(seam_map, "_load_tiff_overview", overview)
    assert seam_map.rgb_thumb_jpeg(job) == stored


def test_thumb_is_not_cached_while_ortho_is_still_growing(tmp_path, monkeypatch):
    job, products, ortho = _job(tmp_path, status="running")

    def overview(path, max_side=480):
        ortho.write_bytes(ortho.read_bytes() + b"more")
        empty = np.zeros((4, 4, 4), np.uint8)
        return empty, 4, 4, 4, "uint8"

    monkeypatch.setattr(seam_map, "_load_tiff_overview", overview)
    data = seam_map.rgb_thumb_jpeg(job)
    assert _color(data) == (236, 236, 236)
    assert not (products / seam_map.THUMB_NAME).exists()


def test_flat_placeholder_newer_than_ortho_is_not_trusted(tmp_path, monkeypatch):
    job, products, ortho = _job(tmp_path, status="succeeded")
    thumb = products / seam_map.THUMB_NAME
    thumb.write_bytes(_jpeg((236, 236, 236)))
    os.utime(ortho, (thumb.stat().st_mtime - 30, thumb.stat().st_mtime - 30))
    calls = {"n": 0}

    def overview(path, max_side=480):
        calls["n"] += 1
        rgb = np.zeros((4, 4, 3), np.uint8)
        rgb[:] = (20, 30, 40)
        return rgb, 4, 4, 3, "uint8"

    monkeypatch.setattr(seam_map, "_load_tiff_overview", overview)
    data = seam_map.rgb_thumb_jpeg(job)
    assert calls["n"] == 1
    assert _color(data) != (236, 236, 236)
