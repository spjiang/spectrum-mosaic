"""格网范围取 POS，不随空三后外方位漂移。"""

from pathlib import Path

import numpy as np

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.pipeline import pos_grid_views
from ms_mosaic.scene import Block, ImageRef


def test_pos_grid_views_uses_block_pose_not_at():
    block = Block(crs="EPSG:32650", ground_z=1760.0)
    cam = Camera.initial("Color", 400, 300, kind="rgb")
    block.cameras = {"Color": cam}
    pos = Pose.from_ypr(np.array([674200.0, 2620200.0, 1870.0]), 0.0, -90.0, 0.0)
    at = Pose.from_ypr(np.array([674500.0, 2620500.0, 1870.0]), 0.0, -90.0, 0.0)
    block.images.append(ImageRef(0, 0, "Color", Path("/in/a.tif"), 0))
    block.poses[0] = pos
    cameras, poses = pos_grid_views(block, {0: at})
    assert poses[0] is pos
    assert cameras[0] is cam
    np.testing.assert_allclose(poses[0].center, pos.center)
