"""显示拉伸：偏斜 DN 线性拉伸会偏暗，伽马后中间调应抬起来。"""

from __future__ import annotations

import numpy as np

from pb_mosaic.mosaic.merge import NODATA
from pb_mosaic.products.preview import STRETCH_GAMMA, apply_stretch, stretch_rgb


def test_gamma_brightens_midtones() -> None:
    # 线性后落在 0.1 的中间调：纯线性约 25，伽马 0.7 应明显抬高，但不到发白
    linear = apply_stretch(np.array([[10.0]]), 0.0, 100.0, gamma=1.0)[0, 0]
    gamma = apply_stretch(np.array([[10.0]]), 0.0, 100.0, gamma=STRETCH_GAMMA)[0, 0]
    assert linear < 30 and linear * 1.3 < gamma < 80


def test_skewed_dn_preview_not_crushed() -> None:
    rng = np.random.default_rng(0)
    h, w = 200, 200
    # 大量低值 + 少量亮尾，模拟农田 DN
    base = rng.lognormal(mean=5.2, sigma=0.35, size=(h, w, 3)).astype(np.float32)
    base[:, :, 2] *= 0.15  # 蓝通道更暗
    bright = rng.uniform(800, 2000, size=(20, 20, 3))
    base[90:110, 90:110] = bright
    rgb = np.clip(base, 0, 4000).astype(np.uint16)
    valid = np.ones((h, w), dtype=bool)
    rgb[:10] = NODATA
    valid[:10] = False

    out, _ = stretch_rgb(rgb, valid)
    mid = out[valid]
    # 比纯线性亮，又不能抬到发白
    assert 55 < np.median(mid[:, 1]) < 120
    assert mid[:, 1].mean() < 140
