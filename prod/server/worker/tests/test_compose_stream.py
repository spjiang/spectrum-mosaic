"""正射流式出图：落盘累加 + 条带写出，必须与整幅内存路径结果一致。

任务 #18 在 Color 正射 1510/1517 块被系统杀掉（exit -9）：2.1 亿像元的整幅累加与
归一化峰值约 13 GB。这里锁住「改成流式后成品不变」。
"""

from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt

from ms_mosaic.compose import BandRaster, _sweep_stale_scratch, write_band_product
from ms_mosaic.dense import _sample_bilinear
from ms_mosaic.grid import Grid
from ms_mosaic.products import RGB_BAND, apply_coverage_mask, group_name, quantize, rgb_with_alpha

CRS = "EPSG:32650"


def _grid(h: int, w: int, gsd: float = 0.05) -> Grid:
    return Grid.from_bounds((674000.0, 2620000.0, 674000.0 + w * gsd, 2620000.0 + h * gsd), gsd, CRS)


def _rgb_mosaic(h: int, w: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = rng.uniform(1, 250, (3, h, w)).astype(np.float32)
    m[:, :5, :] = np.nan  # 外侧无数据
    m[:, 20:24, 10:13] = np.nan  # 内部小孔，应被最近像元补上
    m[:, 40:42, 30:31] = 0.0  # RGB 全 0 当未知
    m[:, -3:, -6:] = np.nan
    return m


def _old_path(mosaic: np.ndarray, band: str) -> np.ndarray:
    quantized, valid = quantize(mosaic, band)
    quantized, valid = apply_coverage_mask(quantized, valid, trim_m=0.0, gsd=0.05)
    if band == RGB_BAND:
        return rgb_with_alpha(quantized, valid)
    return quantized[:1]


def test_stream_write_matches_whole_array_single_strip(tmp_path: Path):
    h, w = 64, 48
    grid = _grid(h, w)
    mosaic = _rgb_mosaic(h, w)
    path = tmp_path / group_name(RGB_BAND)
    write_band_product(mosaic, grid, RGB_BAND, path, strip_rows=h)
    with rasterio.open(path) as ds:
        got = ds.read()
    np.testing.assert_array_equal(got, _old_path(mosaic, RGB_BAND))


def test_stream_write_small_strips_same_mask_and_nearest_distance(tmp_path: Path, monkeypatch):
    """条带很小、补洞跨条带时：掩膜逐格一致，补进来的值来自全局最近的已知像元。"""
    monkeypatch.setattr("ms_mosaic.compose.INPAINT_MARGIN_ROWS", 1)
    h, w = 60, 50
    grid = _grid(h, w)
    # 多光谱：每格值唯一，可由填充值反推来源像元
    mosaic = (np.arange(h * w, dtype=np.float32).reshape(1, h, w) + 1.0)
    mosaic[:, 25:33, 10:30] = np.nan  # 跨好几个条带的内部空洞
    band = "450nm"
    path = tmp_path / group_name(band)
    write_band_product(mosaic, grid, band, path, strip_rows=4)
    with rasterio.open(path) as ds:
        got = ds.read(1).astype(np.int64)
    ref = _old_path(mosaic, band)[0].astype(np.int64)
    np.testing.assert_array_equal(got > 0, ref > 0)
    known = np.isfinite(mosaic[0])
    dist = distance_transform_edt(~known)
    hole = ~known & (ref > 0)
    src = got[hole] - 1
    sr, sc = src // w, src % w
    hr, hc = np.nonzero(hole)
    np.testing.assert_allclose(np.hypot(sr - hr, sc - hc), dist[hole])
    np.testing.assert_array_equal(got[known], ref[known])


def test_band_raster_matches_in_memory_accumulation(tmp_path: Path):
    h, w = 40, 30
    grid = _grid(h, w)
    rng = np.random.default_rng(3)
    raster = BandRaster(grid, 3, scratch_root=tmp_path)
    acc = np.zeros((3, h, w), np.float32)
    wsum = np.zeros((h, w), np.float32)
    for window in [(0, 0, 25, 20), (15, 10, 25, 20)]:
        r0, c0, rr, cc = window
        tile = rng.uniform(0, 255, (3, rr, cc)).astype(np.float32)
        tile[:, :2, :2] = np.nan
        wr = rng.uniform(0.1, 1.0, (rr, cc)).astype(np.float32)
        raster.add_tile(window, tile, np.zeros((rr, cc), np.int16), [7, 9], wr)
        finite = np.isfinite(tile).all(axis=0)
        wt = np.where(finite, wr, 0.0).astype(np.float32)
        acc[:, r0 : r0 + rr, c0 : c0 + cc] += np.nan_to_num(tile) * wt
        wsum[r0 : r0 + rr, c0 : c0 + cc] += wt
    want = np.where(wsum > 1e-6, acc / np.maximum(wsum, 1e-6), np.nan)
    np.testing.assert_allclose(raster.to_array(), want, rtol=1e-6, equal_nan=True)
    np.testing.assert_allclose(raster.strip(10, 20), want[:, 10:20], rtol=1e-6, equal_nan=True)
    assert raster.views == [7, 9]
    assert raster.labels.min() == -1 and raster.labels.max() == 0


def test_tile_labels_are_global_view_indices(tmp_path: Path):
    """两块的局部第 0 张不是同一张影像，整幅标号必须记成 views 里的同一位置。"""
    grid = _grid(10, 20)
    raster = BandRaster(grid, 1, scratch_root=tmp_path)
    tile = np.ones((1, 10, 10), np.float32)
    wr = np.ones((10, 10), np.float32)
    raster.add_tile((0, 0, 10, 10), tile, np.ones((10, 10), np.int16), [7, 9], wr)
    raster.add_tile((0, 10, 10, 10), tile, np.zeros((10, 10), np.int16), [9, 11], wr)
    lab = raster.labels
    assert raster.views == [7, 9, 11]
    assert np.all(lab[:, :10] == 1)
    assert np.all(lab[:, 10:] == 1)
    raster.close()
    d = raster.dir
    raster.close()
    assert not d.exists()


def test_sweep_stale_scratch_removes_dead_pid_dirs(tmp_path: Path):
    dead = tmp_path / "999999_abc"
    dead.mkdir()
    alive = tmp_path / f"{1}_keep"  # pid 1 总是存在
    alive.mkdir()
    _sweep_stale_scratch(tmp_path)
    assert not dead.exists()
    assert alive.exists()


def test_native_dtype_sampling_matches_float32():
    """影像缓存不再转 float32：uint8/uint16 采样结果必须逐位相同。"""
    rng = np.random.default_rng(1)
    img8 = rng.integers(0, 256, (30, 40), dtype=np.uint8)
    img16 = rng.integers(0, 65536, (30, 40), dtype=np.uint16)
    u = rng.uniform(-1, 41, 500)
    v = rng.uniform(-1, 31, 500)
    for img in (img8, img16):
        a = _sample_bilinear(img, u, v)
        b = _sample_bilinear(img.astype(np.float32), u, v)
        assert a.dtype == b.dtype
        np.testing.assert_array_equal(a, b)
