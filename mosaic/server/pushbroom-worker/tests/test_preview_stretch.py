"""显示拉伸：偏斜 DN 线性拉伸会偏暗，伽马后中间调应抬起来。"""

from __future__ import annotations

import numpy as np

from pb_mosaic.mosaic.merge import NODATA
from pb_mosaic.products.preview import STRETCH_GAMMA, apply_stretch, stretch_rgb


def test_gamma_brightens_midtones() -> None:
    # 线性后落在 0.1 的中间调，纯线性约 25，伽马 0.5 应抬到约 80
    linear = apply_stretch(np.array([[10.0]]), 0.0, 100.0, gamma=1.0)[0, 0]
    gamma = apply_stretch(np.array([[10.0]]), 0.0, 100.0, gamma=STRETCH_GAMMA)[0, 0]
    assert linear < 30 and gamma > linear * 2


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
    assert np.median(mid[:, 1]) > 75
    assert mid[:, 1].mean() > 90
