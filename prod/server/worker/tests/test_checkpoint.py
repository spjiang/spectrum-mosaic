from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from ms_mosaic.at import ATResult
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.checkpoint import (
    apply_payload,
    checkpoint_path,
    load_at_payload,
    resolve_sparse,
    save_sparse,
    should_reuse_at,
)
from ms_mosaic.ortho import share_rectification
from ms_mosaic.runner import SparseResult
from ms_mosaic.scene import Block, ImageRef, transfer_band
from ms_mosaic.tracks import Tracks


def _sparse() -> SparseResult:
    cam = Camera(key="Color", width=2048, height=1536, f=2100.0, cx=1024.0, cy=768.0, k1=0.01)
    pose = Pose(rotation=np.eye(3), center=np.array([100.0, 200.0, 50.0]))
    block = Block(crs="EPSG:32647", ground_z=1600.0)
    block.cameras["Color"] = cam
    block.poses[7] = pose
    points = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    at = ATResult(
        cameras={"Color": cam},
        poses={7: pose},
        points=points,
        observations=[],
        residuals_px=np.array([0.2, 0.3]),
        stats={"rms_px": 0.26, "n_points": 2},
    )
    return SparseResult(
        block=block,
        at=at,
        tracks=Tracks(observations=[]),
        image_paths={7: Path("/data/in/a.tif")},
        poses={7: pose},
        camera=cam,
        timings={"at": 12.5, "total": 40.0},
        counts={"n_usable": 1, "n_primary": 1},
        matches=[],
        keypoint_counts={7: 10},
    )


def _catalog_block() -> Block:
    """编目刚建好的测区：内参是初值，外方位是 POS，还没有平差结果。"""
    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.cameras = {
        "Color": Camera.initial("Color", 2048, 1536, kind="rgb"),
        "450nm": Camera.initial("450nm", 2048, 1536, kind="ms"),
        "550nm": Camera.initial("550nm", 2048, 1536, kind="ms"),
    }
    pos = Pose.from_ypr(np.array([673000.0, 2620000.0, 100.0]), 0.0, -90.0, 0.0)
    for index, band in ((0, "Color"), (1, "450nm"), (2, "550nm")):
        block.images.append(ImageRef(index, 10, band, Path(f"/in/{band}.tif"), 0))
        block.poses[index] = pos
    return block


def test_resume_keeps_catalog_cameras_so_bands_still_share_geometry():
    """续跑不得把平差后的内参写回 block。

    transfer_band 把 block.cameras 当作初值，再按主波段焦距比例缩放。
    写回之后同一套比例会再乘一次，各波段焦距互不相同，几何共用被判失败。
    """
    catalog = _catalog_block()
    fresh = _catalog_block()
    color = catalog.cameras["Color"]
    ba_color = color.with_vector(
        np.array([color.f * 1.08, color.cx + 4.0, color.cy - 2.0, -0.12, 0.19, 0, 0, 0, 0, 0])
    )
    ba_450 = catalog.cameras["450nm"].with_vector(
        np.array([2368.0, 1036.0, 784.0, -0.121, 0.196, 0, 0, 0, 0, 0])
    )
    ba_550 = catalog.cameras["550nm"].with_vector(
        np.array([2385.0, 1027.0, 780.0, -0.131, 0.202, 0, 0, 0, 0, 0])
    )
    at_pose = Pose.from_ypr(np.array([673001.2, 2620002.0, 99.4]), 1.0, -89.2, 0.1)
    payload = {
        "camera_objs": {"Color": ba_color, "450nm": ba_450, "550nm": ba_550},
        "camera_key": "Color",
        "poses": {0: at_pose, 1: at_pose, 2: at_pose},
        "points": np.zeros((1, 3)),
        "residuals_px": np.zeros(0),
        "stats": {},
        "image_path_map": {},
        "counts": {},
    }
    loaded = apply_payload(catalog, payload)

    np.testing.assert_allclose(catalog.cameras["450nm"].to_vector(), fresh.cameras["450nm"].to_vector())
    np.testing.assert_allclose(catalog.cameras["Color"].to_vector(), fresh.cameras["Color"].to_vector())
    np.testing.assert_allclose(catalog.poses[0].center, fresh.poses[0].center)
    assert loaded.at.cameras["450nm"].f == ba_450.f
    np.testing.assert_allclose(loaded.poses[0].center, at_pose.center)

    at_cameras = {"Color": ba_color}
    at_poses = {0: at_pose}

    def group(block, band):
        cameras, poses, _paths = transfer_band(block, at_cameras, at_poses, band)
        shot_of = {im.index: im.shot_index for im in block.by_band(band)}
        return cameras, poses, shot_of

    resumed_450, _, _ = group(loaded.block, "450nm")
    fresh_450, _, _ = group(fresh, "450nm")
    np.testing.assert_allclose(resumed_450[1].to_vector(), fresh_450[1].to_vector())
    assert share_rectification([group(loaded.block, "450nm"), group(loaded.block, "550nm")])


def test_checkpoint_path_sits_beside_features_dir(tmp_path: Path):
    assert checkpoint_path(tmp_path / "cache" / "features") == tmp_path / "cache" / "at_result.npz"


def test_should_reuse_at_only_after_s2_when_file_exists(tmp_path: Path):
    path = tmp_path / "at_result.npz"
    assert should_reuse_at("S5_ortho", path) is False
    path.write_bytes(b"x")
    assert should_reuse_at("S2_at", path) is False
    assert should_reuse_at("S3_dense", path) is True
    assert should_reuse_at("S5_ortho", path) is True


def test_save_and_load_payload_roundtrip(tmp_path: Path):
    sparse = _sparse()
    path = tmp_path / "at_result.npz"
    save_sparse(path, sparse, max_index=20, max_frames=8)
    payload = load_at_payload(path)
    assert payload["max_index"] == 20
    assert payload["max_frames"] == 8
    assert payload["stats"]["rms_px"] == 0.26
    np.testing.assert_allclose(payload["points"], sparse.at.points)
    loaded = apply_payload(sparse.block, payload)
    assert loaded.camera.f == 2100.0
    assert loaded.camera.k1 == 0.01
    np.testing.assert_allclose(loaded.poses[7].center, [100.0, 200.0, 50.0])
    np.testing.assert_allclose(loaded.at.points, sparse.at.points)
    assert loaded.at.stats["n_points"] == 2


def test_resolve_sparse_skips_run_when_checkpoint_exists(tmp_path: Path):
    cache = tmp_path / "cache" / "features"
    cache.mkdir(parents=True)
    save_sparse(checkpoint_path(cache), _sparse())
    run = MagicMock(side_effect=AssertionError("must not rerun AT"))
    got = resolve_sparse(
        Path("/data/in"),
        cache,
        start_stage="S5_ortho",
        run_sparse_fn=run,
        rebuild_fn=lambda _inp, **_k: _sparse().block,
    )
    run.assert_not_called()
    assert got.camera.f == 2100.0


def test_resolve_sparse_runs_and_writes_checkpoint(tmp_path: Path):
    cache = tmp_path / "cache" / "features"
    cache.mkdir(parents=True)
    run = MagicMock(return_value=_sparse())
    got = resolve_sparse(
        Path("/data/in"),
        cache,
        start_stage="S0_io",
        max_index=9,
        run_sparse_fn=run,
    )
    run.assert_called_once()
    assert checkpoint_path(cache).is_file()
    assert got.counts["n_usable"] == 1
    payload = load_at_payload(checkpoint_path(cache))
    assert payload["max_index"] == 9
