"""正射块：真正射 → 图割拼接线 → 曝光补偿 → 多频段融合。

这是交付链路里「出图」的核心一步。块之间独立，可按格网并行；主进程负责
把块粘回整幅并写盘，避免多进程同时写同一 GeoTIFF。
"""

from __future__ import annotations

import os
import tempfile
from functools import partial
from pathlib import Path

import numpy as np

from ms_mosaic.blend import (
    OverlapStats,
    accumulate_overlap,
    apply_gains,
    merge_overlap,
    multiband_blend,
    solve_gains,
)
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.dsm import resample_height
from ms_mosaic.grid import Grid
from ms_mosaic.ortho import NativeImageCache, OrthoConfig, orthorectify_tile
from ms_mosaic.parallel import CACHE_PER_WORKER, map_tiles
from ms_mosaic.products import (
    RGB_BAND,
    RasterWriter,
    apply_coverage_mask,
    band_dtype,
    group_name,
    quantize,
    rgb_with_alpha,
)
from ms_mosaic.seamline import SeamConfig, optimal_labels

ORTHO_TILE = 384
# 块与块之间必须有足够重叠，并且按权重融合。旧写法 overlap=16 再硬裁内芯，
# 每块独立解增益、独立选拼接线，相邻 384 格亮度可差 70，图上就是左侧那些方块。
ORTHO_OVERLAP = 48


def _ortho_setup(payload: dict):
    ctx = dict(payload)
    path = payload.get("dsm_z_path")
    if path:
        ctx["dsm_z"] = np.load(path, mmap_mode="r")
    ctx["images"] = NativeImageCache(payload["paths"], limit=CACHE_PER_WORKER)
    return ctx


def _share_array(arr: np.ndarray) -> Path:
    """子进程 mmap 同一份数组，避免 spawn 把 DSM 复制 12 份。"""
    fd, name = tempfile.mkstemp(prefix="ms_dsm_", suffix=".npy")
    os.close(fd)
    path = Path(name)
    np.save(path, arr)
    return path


def _feather_weight(
    window: tuple[int, int, int, int], overlap: int, grid: Grid
) -> np.ndarray:
    """窗口内的拼接权重：整幅边缘为 1，与邻块重叠处线性过渡到 0。

    相邻两块的窗口共享 2*overlap 列/行。权重在共享带上从 1 降到 0，
    贴回去时做加权平均，块界就不会留下方块。
    """
    row0, col0, rows, cols = window
    wr = np.ones((rows, cols), np.float32)
    if overlap <= 0:
        return wr
    rr = np.arange(rows, dtype=np.float32)[:, None]
    cc = np.arange(cols, dtype=np.float32)[None, :]
    dist = np.full((rows, cols), np.float32(2 * overlap), np.float32)
    if row0 > 0:
        dist = np.minimum(dist, rr)
    if col0 > 0:
        dist = np.minimum(dist, cc)
    if row0 + rows < grid.height:
        dist = np.minimum(dist, rows - rr)
    if col0 + cols < grid.width:
        dist = np.minimum(dist, cols - cc)
    return np.clip(dist / float(2 * overlap), 0.0, 1.0).astype(np.float32)


def _stack_for_window(ctx: dict, window: tuple[int, int, int, int]):
    z = resample_height(ctx["dsm_z"], ctx["dsm_grid"], ctx["grid"], window)
    return orthorectify_tile(
        ctx["grid"], window, z, ctx["cameras"], ctx["poses"], ctx["images"], ctx["ortho_cfg"]
    )


def _overlap_fn(ctx: dict, window: tuple[int, int, int, int]):
    """第一遍：只统计重叠亮度，不融合。增益必须等全图汇总后再解。"""
    stack = _stack_for_window(ctx, window)
    if stack.pixels.shape[0] == 0:
        return OverlapStats()
    acc = OverlapStats()
    accumulate_overlap(stack, acc, channel=None)
    return acc


def _ortho_fn(ctx: dict, window: tuple[int, int, int, int]):
    stack = _stack_for_window(ctx, window)
    gains = ctx.get("gains") or {}
    if gains:
        stack = apply_gains(stack, gains)
    labels = optimal_labels(stack, ctx["seam_cfg"])
    mosaic = multiband_blend(stack, labels)
    return mosaic, labels, list(stack.views)


def render_band(
    grid: Grid,
    dsm_z: np.ndarray,
    dsm_grid: Grid,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    paths: dict[int, Path],
    *,
    band: str,
    tile: int = ORTHO_TILE,
    overlap: int = ORTHO_OVERLAP,
    workers: int | None = None,
    gain: bool | None = None,
    log=None,
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """整幅正射（浮点）+ 拼接线标号。标号是块内视角下标，views 是全局影像 id。

    RGB 默认先全图统计重叠再解 Brown & Lowe 全局增益（禁止按块），然后融合。
    多光谱不加增益，与「颜色校正：禁用」一致。
    """
    use_gain = (band == RGB_BAND) if gain is None else bool(gain)
    windows = list(grid.tiles(tile, overlap=overlap))
    shared = _share_array(np.asarray(dsm_z))
    payload = {
        "grid": grid,
        "dsm_z_path": str(shared),
        "dsm_grid": dsm_grid,
        "cameras": cameras,
        "poses": poses,
        "paths": paths,
        "ortho_cfg": OrthoConfig(),
        "seam_cfg": SeamConfig(),
        "gains": {},
    }
    try:
        return _render_band_with_payload(
            grid,
            payload,
            windows,
            band=band,
            overlap=overlap,
            workers=workers,
            use_gain=use_gain,
            log=log,
        )
    finally:
        try:
            shared.unlink()
        except OSError:
            pass


def _render_band_with_payload(
    grid: Grid,
    payload: dict,
    windows: list,
    *,
    band: str,
    overlap: int,
    workers: int | None,
    use_gain: bool,
    log,
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    out_name = group_name(band)
    if use_gain:
        acc = OverlapStats()
        for _, _window, tile_acc in map_tiles(
            windows,
            partial(_ortho_setup, payload),
            _overlap_fn,
            workers=workers,
            log=log,
            label=f"曝光统计 {out_name} ({band})",
        ):
            if tile_acc:
                merge_overlap(acc, tile_acc)
        payload["gains"] = solve_gains(acc)
        if log is not None:
            vals = list(payload["gains"].values())
            if vals:
                p = np.percentile(vals, [10, 50, 90])
                log(
                    f"全局增益 {out_name} ({band}) {len(vals)} 张，"
                    f"p10/p50/p90={p[0]:.3f}/{p[1]:.3f}/{p[2]:.3f}"
                )
            else:
                log(f"全局增益 {out_name} ({band})：重叠不足，保持 1.0")
    sample_bands = 3 if band == RGB_BAND else 1
    acc = np.zeros((sample_bands, grid.height, grid.width), np.float32)
    wsum = np.zeros(grid.shape, np.float32)
    labels = np.full(grid.shape, -1, np.int16)
    label_w = np.zeros(grid.shape, np.float32)
    views_all: list[int] = []
    for _, window, (tile_m, tile_l, views) in map_tiles(
        windows,
        partial(_ortho_setup, payload),
        _ortho_fn,
        workers=workers,
        log=log,
        label=f"正射 {out_name} ({band})",
    ):
        if tile_m.size == 0:
            continue
        if tile_m.shape[0] != acc.shape[0]:
            if tile_m.shape[0] == 1 and acc.shape[0] == 3:
                tile_m = np.repeat(tile_m, 3, axis=0)
            else:
                continue
        row0, col0, rows, cols = window
        wr = _feather_weight(window, overlap, grid)
        finite = np.isfinite(tile_m).all(axis=0)
        w = np.where(finite, wr, 0.0).astype(np.float32)
        sl = (slice(row0, row0 + rows), slice(col0, col0 + cols))
        acc[(slice(None),) + sl] += np.nan_to_num(tile_m, nan=0.0) * w
        wsum[sl] += w
        better = w > label_w[sl]
        labels[sl] = np.where(better, tile_l, labels[sl])
        label_w[sl] = np.where(better, w, label_w[sl])
        for v in views:
            if v not in views_all:
                views_all.append(v)
    good = wsum > 1e-6
    mosaic = np.where(good, acc / np.maximum(wsum, 1e-6), np.nan).astype(np.float32)
    return mosaic, labels, views_all


def write_band_product(
    mosaic: np.ndarray,
    grid: Grid,
    band: str,
    path: Path,
    *,
    coverage: np.ndarray | None = None,
    trim_m: float = 0.0,
) -> Path:
    quantized, valid = quantize(mosaic, band)
    if coverage is not None:
        valid = np.asarray(coverage, bool) & valid
    quantized, valid = apply_coverage_mask(
        quantized, valid, trim_m=trim_m, gsd=grid.gsd
    )
    if band == RGB_BAND:
        data = rgb_with_alpha(quantized, valid)
        with RasterWriter(path, grid, count=4, dtype="uint8", alpha=True) as w:
            w.write((0, 0, grid.height, grid.width), data)
    else:
        with RasterWriter(path, grid, count=1, dtype=band_dtype(band)) as w:
            w.write((0, 0, grid.height, grid.width), quantized[:1])
    return Path(path)
