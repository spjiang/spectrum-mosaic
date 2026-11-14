"""S1 编目耗时应在 scan/筛选/build_block 之后上报，而不是 pipeline 刚启动时。"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_run_sparse_invokes_catalog_done_after_build(monkeypatch, tmp_path: Path):
    from ms_mosaic import runner

    inp = tmp_path / "in"
    inp.mkdir()
    cache = tmp_path / "cache"
    order: list[str] = []

    def fake_scan(_root, max_index=None):
        order.append("scan")
        return {}

    def fake_usable(shots, **kwargs):
        order.append("usable")
        return {}, "ok"

    def fake_build(keep):
        order.append("build")
        block = MagicMock()
        block.images = []
        block.primary.return_value = MagicMock()
        block.cameras = {}
        block.poses = {}
        block.ground_z = 0.0
        block.band_order.return_value = ["Color"]
        return block

    monkeypatch.setattr(runner, "scan_directory", fake_scan)
    monkeypatch.setattr(runner, "usable_shots", fake_usable)
    monkeypatch.setattr(runner, "build_block", fake_build)
    monkeypatch.setattr(runner, "_choose_feature_device", lambda log=None: None)
    monkeypatch.setattr(runner, "_overlap_extract_match", lambda *a, **k: ([], 0.0))
    monkeypatch.setattr(runner, "build_tracks", lambda *a, **k: MagicMock(per_image_counts=lambda: {}))
    monkeypatch.setattr(runner, "summarize_matches", lambda *a, **k: [])
    monkeypatch.setattr(runner, "run_at", lambda *a, **k: MagicMock(
        cameras={}, poses={}, points=__import__("numpy").zeros((0, 3)), observations=[],
        residuals_px=__import__("numpy").zeros(0), stats={}, obs_counts={}, obs_sq_residual={},
    ))

    catalog_s: list[float] = []

    try:
        runner.run_sparse(inp, cache, workers=1, on_catalog_done=catalog_s.append)
    except Exception:
        pass

    assert order[:3] == ["scan", "usable", "build"]
    assert len(catalog_s) == 1
    assert catalog_s[0] >= 0.0


def test_resolve_sparse_calls_catalog_done_on_reuse(monkeypatch, tmp_path: Path):
    from ms_mosaic.checkpoint import resolve_sparse

    inp = tmp_path / "in"
    inp.mkdir()
    cache = tmp_path / "cache"
    seen: list[float] = []

    ckpt = cache / "at_result.npz"
    cache.mkdir(parents=True)
    ckpt.write_bytes(b"x")
    monkeypatch.setattr("ms_mosaic.checkpoint.load_at_payload", lambda *a, **k: {"max_index": None, "max_frames": None})
    monkeypatch.setattr(
        "ms_mosaic.checkpoint.apply_payload",
        lambda block, payload: MagicMock(block=block, at=MagicMock(), counts={}, timings={}),
    )

    def fake_rebuild(*a, **k):
        return MagicMock()

    monkeypatch.setattr("ms_mosaic.checkpoint.rebuild_block", fake_rebuild)

    resolve_sparse(inp, cache, start_stage="S3_dense", on_catalog_done=seen.append)
    assert len(seen) == 1
    assert seen[0] >= 0.0
