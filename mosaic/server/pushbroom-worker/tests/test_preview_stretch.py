"""显示拉伸：偏斜 DN 线性拉伸会偏暗，伽马后中间调应抬起来。"""

from __future__ import annotations

import numpy as np

from pb_mosaic.mosaic.merge import NODATA
from pb_mosaic.products.preview import STRETCH_GAMMA, apply_stretch, stretch_rgb


def test_gamma_brightens_midtones() -> None:
    linear = apply_stretch(np.array([[10.0]]), 0.0, 100.0, gamma=1.0, gain=1.0)[0, 0]
    gamma = apply_stretch(np.array([[10.0]]), 0.0, 100.0, gamma=STRETCH_GAMMA, gain=1.0)[0, 0]
    assert linear < 30 and gamma > linear


def test_skewed_dn_preview_not_crushed() -> None:
    rng = np.random.default_rng(0)
    h, w = 200, 200
    base = rng.lognormal(mean=5.2, sigma=0.35, size=(h, w, 3)).astype(np.float32)
    base[:, :, 2] *= 0.15
    bright = rng.uniform(800, 2000, size=(20, 20, 3))
    base[90:110, 90:110] = bright
    rgb = np.clip(base, 0, 4000).astype(np.uint16)
    valid = np.ones((h, w), dtype=bool)
    rgb[:10] = NODATA
    valid[:10] = False

    out, _ = stretch_rgb(rgb, valid)
    mid = out[valid]
    # 绿通道应高于红（通道增益），且不要整体抬飞
    assert np.median(mid[:, 1]) > np.median(mid[:, 0])
    assert 50 < np.median(mid[:, 1]) < 140
