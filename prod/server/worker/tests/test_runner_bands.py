"""全波段稀疏：组内选对、不跨波段匹配。"""

from pathlib import Path

import numpy as np

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.runner import _band_pair_candidates
from ms_mosaic.scene import Block, ImageRef


def _pose(x: float, y: float = 0.0, z: float = 80.0) -> Pose:
    return Pose.from_ypr(np.array([x, y, z]), 0.0, -90.0, 0.0)


def test_band_pair_candidates_stay_within_band():
    cam_c = Camera.initial("Color", 1440, 1080, kind="rgb")
    cam_g = Camera.initial("550nm", 1440, 1080, kind="ms")
    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.cameras = {"Color": cam_c, "550nm": cam_g}
    # 两波段各 3 张，沿航迹重叠；索引交错，若跨波段选对会混进 Color↔550nm
    specs = [
        (0, 0, "Color", 0.0),
        (1, 0, "550nm", 0.0),
        (2, 1, "Color", 12.0),
        (3, 1, "550nm", 12.0),
        (4, 2, "Color", 24.0),
        (5, 2, "550nm", 24.0),
    ]
    for idx, shot, band, x in specs:
        block.images.append(ImageRef(idx, shot, band, Path(f"{band}_{shot}.tif"), 0 if band == "Color" else 1))
        block.poses[idx] = _pose(x)
        block.gps[idx] = np.array([x, 0.0, 80.0])

    cands = _band_pair_candidates(block, ground_z=0.0)
    assert cands, "同波段航迹应选出重叠像对"
    band_of = {im.index: im.band for im in block.images}
    for c in cands:
        assert band_of[c.i] == band_of[c.j]
    color_pairs = {(c.i, c.j) for c in cands if band_of[c.i] == "Color"}
    green_pairs = {(c.i, c.j) for c in cands if band_of[c.i] == "550nm"}
    assert color_pairs
    assert green_pairs
    assert color_pairs.isdisjoint(green_pairs)
