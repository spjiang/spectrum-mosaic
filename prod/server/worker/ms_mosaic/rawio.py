"""原始影像读取。输入目录只读，这里所有函数都不写盘。

RGB 是 8bit JPG，多光谱是 16bit 单通道 TIF。特征提取需要统一的 8bit 灰度，
而正射与融合需要保留原始位深，所以拆成两个入口。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# 16bit 多光谱拉伸到 8bit 时的裁剪分位数。用分位数而非极值，避免个别坏点压扁动态范围。
STRETCH_LOW_PCT = 0.5
STRETCH_HIGH_PCT = 99.5


def read_native(path: Path) -> np.ndarray:
    """按原始位深读取。JPG 返回 (H, W, 3) uint8，TIF 返回 (H, W) uint16。"""
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        from PIL import Image

        with Image.open(path) as im:
            return np.asarray(im.convert("RGB"))
    import tifffile

    arr = tifffile.imread(path)
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    return arr


def to_gray8(arr: np.ndarray) -> np.ndarray:
    """任意输入 → 适合特征提取的 8bit 灰度。"""
    if arr.ndim == 3:
        # BT.601 luma，和 OpenCV 的 COLOR_RGB2GRAY 一致
        gray = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
        return np.clip(gray, 0, 255).astype(np.uint8)
    if arr.dtype == np.uint8:
        return arr
    finite = arr[np.isfinite(arr)] if np.issubdtype(arr.dtype, np.floating) else arr
    lo, hi = np.percentile(finite, [STRETCH_LOW_PCT, STRETCH_HIGH_PCT])
    if hi <= lo:
        lo, hi = float(np.min(arr)), float(np.max(arr))
    if hi <= lo:
        return np.zeros(arr.shape, np.uint8)
    scaled = (arr.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def read_gray8(path: Path) -> np.ndarray:
    return to_gray8(read_native(path))


def image_size(path: Path) -> tuple[int, int]:
    """返回 (width, height)，尽量不解码整张图。"""
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        from PIL import Image

        with Image.open(path) as im:
            return int(im.width), int(im.height)
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        return int(page.imagewidth), int(page.imagelength)
