"""分块并行：空间切段必须邻近，串行与多进程结果必须一致。"""

from pathlib import Path

import numpy as np
import pytest

from ms_mosaic.grid import Grid
from ms_mosaic.parallel import map_tiles, spatial_chunks, split_ortho_pools


CRS = "EPSG:32647"


def _empty_setup():
    return None


def _window_checksum(_ctx, window):
    row0, col0, rows, cols = window
    return int(row0) * 1_000_003 + int(col0) * 1_009 + int(rows) * 17 + int(cols)


def test_spatial_chunks_cover_every_window_once():
    windows = [(r, c, 8, 8) for r in range(0, 32, 8) for c in range(0, 24, 8)]
    chunks = spatial_chunks(windows, 3)
    flat = [i for c in chunks for i in c]
    assert sorted(flat) == list(range(len(windows)))
    assert len(chunks) == 3


def test_spatial_chunks_keep_neighbors_together():
    """同一段内的块应按行优先挨在一起，而不是按进程号轮转。"""
    windows = [(r, c, 4, 4) for r in range(0, 16, 4) for c in range(0, 16, 4)]
    chunks = spatial_chunks(windows, 2)
    for chunk in chunks:
        ordered = [windows[i] for i in chunk]
        keys = [(w[0], w[1]) for w in ordered]
        assert keys == sorted(keys)


def test_map_tiles_serial_visits_all_windows():
    windows = [(0, 0, 2, 2), (0, 2, 2, 2), (2, 0, 2, 2)]
    got = list(map_tiles(windows, _empty_setup, _window_checksum, workers=1))
    assert [k for k, _, _ in got] == [0, 1, 2]
    assert [w for _, w, _ in got] == windows


def test_map_tiles_parallel_matches_serial():
    windows = [(r, c, 6, 6) for r in range(0, 24, 6) for c in range(0, 18, 6)]
    serial = {
        k: result
        for k, _, result in map_tiles(windows, _empty_setup, _window_checksum, workers=1)
    }
    parallel = {
        k: result
        for k, _, result in map_tiles(windows, _empty_setup, _window_checksum, workers=2)
    }
    assert serial == parallel


def test_paste_tile_drops_overlap_halo():
    from ms_mosaic.dense import paste_tile

    grid = Grid.from_bounds((0.0, 0.0, 20.0, 16.0), 1.0, CRS)
    dst = np.full(grid.shape, np.nan, np.float32)
    overlap = 2
    # 第二块从 col=8 起，带 2 格重叠，有效区应从 col=10 开始写入
    window = (0, 8, 16, 12)  # col0=8, 写到 col 20
    tile = np.full((16, 12), 7.0, np.float32)
    paste_tile(dst, tile, window, overlap, grid)
    assert np.isnan(dst[:, :10]).all()
    assert (dst[:, 10:] == 7.0).all()


def test_split_ortho_pools_keeps_bands_serial_when_few_workers():
    assert split_ortho_pools(7, 1) == (1, 1)
    assert split_ortho_pools(7, 2) == (1, 2)


def test_resolve_workers_stays_inside_free_ram(monkeypatch):
    """36GB 预算也不能按预算把进程开到超过剩余内存。14 进程会把正射杀掉。"""
    from ms_mosaic.parallel import resolve_workers

    monkeypatch.setattr("ms_mosaic.parallel.cpu_cap", lambda requested=None: 15 if requested is None else min(15, int(requested)))
    monkeypatch.setattr("ms_mosaic.parallel.job_memory_gb", lambda: 36.0)
    monkeypatch.setattr("ms_mosaic.parallel.resolve_memory_budget", lambda gb: int(float(gb) * 1024**3))
    monkeypatch.setattr("ms_mosaic.memory.available_bytes", lambda: int(18 * 1024**3))
    monkeypatch.setattr("ms_mosaic.parallel.process_rss_bytes", lambda: 0)
    # 剩余 18GB，扣 1.5GB 预留，按每进程 4GB，得到 4
    assert resolve_workers(None) == 4


def test_resolve_workers_uses_per_worker_estimate(monkeypatch):
    """按实估单进程内存放进程：10GB 剩余、每进程 1.5GB → 5 进程，而不是固定 4GB 压成 2。"""
    from ms_mosaic.parallel import resolve_workers, tile_worker_mb

    monkeypatch.setattr("ms_mosaic.parallel.cpu_cap", lambda requested=None: 8 if requested is None else min(8, int(requested)))
    monkeypatch.setattr("ms_mosaic.parallel.job_memory_gb", lambda: 23.4)
    monkeypatch.setattr("ms_mosaic.parallel.resolve_memory_budget", lambda gb: int(float(gb) * 1024**3))
    monkeypatch.setattr("ms_mosaic.parallel.process_rss_bytes", lambda: int(2 * 1024**3))
    monkeypatch.setattr("ms_mosaic.memory.available_bytes", lambda: int(10 * 1024**3))
    per = tile_worker_mb(2048 * 1536 * 3)  # Color 原始位深 48 张 ≈ 432MB + 1GB
    assert 1400 < per < 1500
    assert resolve_workers(None, per_worker_mb=per) == (10 * 1024 - 1536) // per


def test_resolve_workers_subtracts_own_rss_from_budget(monkeypatch):
    from ms_mosaic.parallel import resolve_workers

    monkeypatch.setattr("ms_mosaic.parallel.cpu_cap", lambda requested=None: 8 if requested is None else min(8, int(requested)))
    monkeypatch.setattr("ms_mosaic.parallel.job_memory_gb", lambda: 23.4)
    monkeypatch.setattr("ms_mosaic.parallel.resolve_memory_budget", lambda gb: int(float(gb) * 1024**3))
    monkeypatch.setattr("ms_mosaic.parallel.process_rss_bytes", lambda: int(20 * 1024**3))
    monkeypatch.setattr("ms_mosaic.memory.available_bytes", lambda: int(64 * 1024**3))
    assert resolve_workers(None, per_worker_mb=1024) == max(1, (int(3.4 * 1024) - 1536) // 1024)


def test_resolve_workers_budget_caps_at_seven(monkeypatch):
    from ms_mosaic.parallel import resolve_workers

    monkeypatch.setattr("ms_mosaic.parallel.cpu_cap", lambda requested=None: 15 if requested is None else min(15, int(requested)))
    monkeypatch.setattr("ms_mosaic.parallel.job_memory_gb", lambda: 36.0)
    monkeypatch.setattr("ms_mosaic.parallel.resolve_memory_budget", lambda gb: int(float(gb) * 1024**3))
    monkeypatch.setattr("ms_mosaic.memory.available_bytes", lambda: int(36 * 1024**3))
    monkeypatch.setattr("ms_mosaic.parallel.process_rss_bytes", lambda: 0)
    # 36GB − 1.5GB 预留，按每进程 4GB → 8
    assert resolve_workers(None) == 8


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="需要 Linux /proc")
def test_peak_rss_of_spawned_child_excludes_parent_peak():
    """子进程峰值不能继承主进程的 ru_maxrss，否则日志里的实测峰值没法用来校准。"""
    import multiprocessing

    from ms_mosaic.parallel import peak_rss_mb

    big = np.ones(400 * 2**20 // 8)
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        child = pool.apply(peak_rss_mb)
    del big
    assert child < 300, f"子进程报 {child:.0f} MB，疑似继承了主进程峰值"


def test_split_ortho_pools_runs_several_ms_bands():
    assert split_ortho_pools(7, 8) == (4, 2)
    n_par, per = split_ortho_pools(7, 7)
    assert n_par == 3 and per == 2
    assert n_par * per <= 7
