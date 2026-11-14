"""主波段空三位姿迁移到其余波段：共平台刚体，同一曝光共用外方位。"""

import numpy as np
import pytest

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.scene import BAND_ORDER, Block, ImageRef, transfer_band


def _block():
    block = Block(crs="EPSG:32647")
    block.cameras = {
        "Color": Camera.initial("Color", 2048, 1536, kind="rgb"),
        "550nm": Camera.initial("550nm", 2048, 1536, kind="ms"),
    }
    rgb = ImageRef(index=0, shot_index=10, band="Color", path="rgb.jpg", group=0)
    ms = ImageRef(index=1, shot_index=10, band="550nm", path="g.tif", group=2)
    rgb2 = ImageRef(index=8, shot_index=11, band="Color", path="rgb2.jpg", group=0)
    block.images = [rgb, ms, rgb2]
    block.poses = {
        0: Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.0]), 10.0, -89.0, 0.0),
        1: Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.0]), 10.0, -89.0, 0.0),
        8: Pose.from_ypr(np.array([673020.0, 2620000.0, 1871.0]), 10.0, -89.0, 0.0),
    }
    return block


def test_transfer_band_reuses_primary_pose_of_same_shot():
    block = _block()
    adj = Pose.from_ypr(np.array([673001.5, 2620002.0, 1870.2]), 12.0, -88.5, 0.2)
    cam = Camera.initial("Color", 2048, 1536, kind="rgb").with_vector(
        np.array([2380.0, 1030.0, 770.0, -0.1, 0.05, 0.01, 0.0, 0.0, 0.0, 0.0])
    )
    cameras, poses, paths = transfer_band(block, {"Color": cam}, {0: adj, 8: adj}, "550nm")
    assert list(poses) == [1]
    np.testing.assert_allclose(poses[1].center, adj.center)
    np.testing.assert_allclose(poses[1].rotation, adj.rotation)
    assert paths[1].name == "g.tif"
    assert cameras[1].key == "550nm"


def test_transfer_band_scales_focal_with_primary_calibration():
    block = _block()
    init = Camera.initial("Color", 2048, 1536, kind="rgb")
    cam = init.with_vector(
        np.array([init.f * 1.12, init.cx + 8.0, init.cy - 3.0, -0.12, 0.08, 0.02, 0.0, 0.0, 0.0, 0.0])
    )
    cameras, _, _ = transfer_band(block, {"Color": cam}, {0: block.poses[0]}, "550nm")
    ms = cameras[1]
    ms_init = Camera.initial("550nm", 2048, 1536, kind="ms")
    assert ms.f == pytest.approx(ms_init.f * 1.12, rel=1e-9)
    assert ms.cx == pytest.approx(ms_init.cx + 8.0)
    assert ms.cy == pytest.approx(ms_init.cy - 3.0)
    assert ms.k1 == pytest.approx(-0.12)


def test_transfer_band_primary_keeps_adjusted_camera():
    block = _block()
    cam = Camera.initial("Color", 2048, 1536, kind="rgb").with_vector(
        np.array([2380.0, 1024.0, 768.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    )
    pose = block.poses[0]
    cameras, poses, paths = transfer_band(block, {"Color": cam}, {0: pose, 8: pose}, "Color")
    assert cameras[0] is cam
    assert set(poses) == {0, 8}
    assert paths[0].name == "rgb.jpg"


def test_transfer_band_skips_shots_without_primary_pose():
    block = _block()
    cam = Camera.initial("Color", 2048, 1536, kind="rgb")
    _, poses, _ = transfer_band(block, {"Color": cam}, {}, "550nm")
    assert poses == {}


def test_band_order_matches_limapper_groups():
    assert BAND_ORDER[0] == "Color"
    assert BAND_ORDER[-1] == "850nm"
    assert len(BAND_ORDER) == 8
