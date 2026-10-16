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
from ms_mosaic.runner import SparseResult
from ms_mosaic.scene import Block
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
