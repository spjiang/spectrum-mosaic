"""GPU 正射对拍：xp 版走 numpy 时必须与 CPU 版逐位相同；走 CuPy 时按设计容差验收。"""

import numpy as np
import pytest

from ms_mosaic import ortho, ortho_gpu
from ms_mosaic.grid import Grid
from synthetic import NADIR_GRID, MemoryCache, render_survey

CRS = "EPSG:32647"
WALL = (673034.0, 673036.0, 2620010.0, 2620050.0, 25.0)


def _terrain_case(n_bands=1, relief=12.0):
    survey = render_survey(NADIR_GRID, relief=relief, n_bands=n_bands)
    grid = Grid.from_bounds((673026.0, 2620026.0, 673034.0, 2620034.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    z = survey.terrain(gx, gy).astype(np.float32)
    return survey, grid, z


def _wall_case():
    survey = render_survey(NADIR_GRID, relief=0.0, wall=WALL)
    grid = Grid.from_bounds((673028.0, 2620024.0, 673042.0, 2620036.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    z = survey.terrain(gx, gy).astype(np.float32)
    z[:3, :5] = np.nan
    return survey, grid, z


def _both(survey, grid, z, **kw):
    window = (0, 0, grid.height, grid.width)
    cpu = ortho.orthorectify_tile(grid, window, z, survey.cameras, survey.poses, MemoryCache(survey.images))
    other = ortho_gpu.orthorectify_tile(
        grid, window, z, survey.cameras, survey.poses, MemoryCache(survey.images), **kw
    )
    return cpu, other


def _assert_identical(cpu, other):
    assert other.views == cpu.views
    np.testing.assert_array_equal(other.pixels, cpu.pixels)
    np.testing.assert_array_equal(other.scores, cpu.scores)


@pytest.mark.parametrize("case", ["terrain", "wall", "multiband"])
def test_numpy_path_matches_cpu_exactly(case):
    if case == "terrain":
        survey, grid, z = _terrain_case()
    elif case == "wall":
        survey, grid, z = _wall_case()
    else:
        survey, grid, z = _terrain_case(n_bands=3)
    cpu, other = _both(survey, grid, z, device=None)
    assert np.isfinite(cpu.scores).any()
    _assert_identical(cpu, other)


def test_numpy_path_facade_chunks_match_cpu():
    survey, grid, z = _wall_case()
    cpu, other = _both(survey, grid, z, device=None, chunk_points=1)
    _assert_identical(cpu, other)


def test_numpy_path_empty_without_elevation():
    survey, grid, z = _terrain_case()
    z[:] = np.nan
    cpu, other = _both(survey, grid, z, device=None)
    assert other.views == [] and cpu.views == []
    assert other.pixels.shape == cpu.pixels.shape


def test_tile_on_cpu_device_uses_cpu(monkeypatch):
    survey, grid, z = _terrain_case()
    called = []
    monkeypatch.setattr(ortho_gpu, "orthorectify_tile", lambda *a, **k: called.append(1))
    window = (0, 0, grid.height, grid.width)
    stack = ortho_gpu.orthorectify_tile_on(
        None, grid, window, z, survey.cameras, survey.poses, MemoryCache(survey.images)
    )
    assert not called
    assert stack.views


class OutOfMemoryError(Exception):
    pass


def test_tile_on_halves_then_falls_back_to_cpu(monkeypatch):
    survey, grid, z = _terrain_case()
    chunks = []

    def fake(*_a, chunk_points, **_k):
        chunks.append(chunk_points)
        raise OutOfMemoryError("out of memory allocating")

    monkeypatch.setattr(ortho_gpu, "orthorectify_tile", fake)
    monkeypatch.setattr(ortho_gpu, "_free_pool", lambda _d: None)
    monkeypatch.setattr(ortho, "FACADE_CHUNK_POINTS", 8)
    logs = []
    window = (0, 0, grid.height, grid.width)
    stack = ortho_gpu.orthorectify_tile_on(
        0, grid, window, z, survey.cameras, survey.poses, MemoryCache(survey.images), log=logs.append
    )
    assert chunks == [8, 4, 2, 1]
    assert "改用 CPU" in logs[0]
    expected = ortho.orthorectify_tile(grid, window, z, survey.cameras, survey.poses, MemoryCache(survey.images))
    np.testing.assert_array_equal(stack.pixels, expected.pixels)


def test_tile_on_reraises_other_errors(monkeypatch):
    survey, grid, z = _terrain_case()
    monkeypatch.setattr(ortho_gpu, "orthorectify_tile", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(ValueError):
        ortho_gpu.orthorectify_tile_on(
            0, grid, (0, 0, grid.height, grid.width), z, survey.cameras, survey.poses, MemoryCache(survey.images)
        )


def _assert_within_tolerance(cpu, gpu_stack):
    """设计第 6 节：覆盖掩膜一致；≥99.9% 像元差 ≤1 DN，最大 ≤2 DN。"""
    assert gpu_stack.views == cpu.views
    np.testing.assert_array_equal(np.isfinite(gpu_stack.scores), np.isfinite(cpu.scores))
    both = np.isfinite(cpu.pixels) & np.isfinite(gpu_stack.pixels)
    diff = np.abs(gpu_stack.pixels[both].astype(np.float64) - cpu.pixels[both])
    assert diff.size > 0
    assert float((diff <= 1.0).mean()) >= 0.999
    assert float(diff.max()) <= 2.0
    np.testing.assert_allclose(gpu_stack.scores[np.isfinite(cpu.scores)], cpu.scores[np.isfinite(cpu.scores)],
                               rtol=1e-5, atol=1e-6)


@pytest.mark.gpu
@pytest.mark.parametrize("case", ["terrain", "wall", "multiband"])
def test_gpu_path_within_tolerance(case):
    if case == "terrain":
        survey, grid, z = _terrain_case()
    elif case == "wall":
        survey, grid, z = _wall_case()
    else:
        survey, grid, z = _terrain_case(n_bands=3)
    cpu, gpu_stack = _both(survey, grid, z, device=0)
    _assert_within_tolerance(cpu, gpu_stack)


@pytest.mark.gpu
def test_gpu_path_facade_chunks_within_tolerance():
    survey, grid, z = _wall_case()
    cpu, gpu_stack = _both(survey, grid, z, device=0, chunk_points=1)
    _assert_within_tolerance(cpu, gpu_stack)
