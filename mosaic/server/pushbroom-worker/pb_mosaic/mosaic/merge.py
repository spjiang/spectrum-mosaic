"""重采样镶嵌：成果格网逐块反算到原始行列，取最近邻 DN，按 BIL 顺序写盘。

重叠区取离航带中线最近的那条航带（像元离狭缝中心最近），接缝落在相邻航带的中间。
光谱不做混合，每个成果像元的 600 个波段都来自同一个原始像元。
内存只和块大小有关：一块成果行 + 这块用到的原始行。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.geom.grid import OutputGrid

log = logging.getLogger("pb_mosaic.merge")

NODATA = 0
# 相邻原始行号相差不超过这么多就合并成一次连续读取
READ_GAP_LINES = 16
# 一块成果最多这么大。块再大读写也不会更连续，反而取消要等更久、进度更新更稀
MAX_BLOCK_BYTES = 256 << 20


class Cancelled(Exception):
    pass


@dataclass
class MosaicStats:
    valid_pixels: int = 0
    strip_pixels: dict[int, int] = field(default_factory=dict)
    source_lines_read: int = 0
    bytes_written: int = 0


@dataclass
class _StripBox:
    geom: StripGeometry
    r0: int
    r1: int
    c0: int
    c1: int


def strip_boxes(geoms: list[StripGeometry], grid: OutputGrid) -> list[_StripBox]:
    boxes: list[_StripBox] = []
    for g in geoms:
        xmin, ymin, xmax, ymax = g.bounds()
        c0, r1 = grid.to_colrow(np.array([xmin]), np.array([ymin]))
        c1, r0 = grid.to_colrow(np.array([xmax]), np.array([ymax]))
        boxes.append(
            _StripBox(
                geom=g,
                r0=max(0, int(np.floor(r0[0])) - 1),
                r1=min(grid.height, int(np.ceil(r1[0])) + 2),
                c0=max(0, int(np.floor(c0[0])) - 1),
                c1=min(grid.width, int(np.ceil(c1[0])) + 2),
            )
        )
    return boxes


def select_block(
    boxes: list[_StripBox], grid: OutputGrid, r0: int, r1: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """成果行 [r0, r1) 每个像元选哪条航带的哪一行哪一列。没有覆盖的 line = -1。"""
    rows = r1 - r0
    best = np.full((rows, grid.width), np.inf, dtype=np.float32)
    line = np.full((rows, grid.width), -1, dtype=np.int64)
    samp = np.zeros((rows, grid.width), dtype=np.int32)
    strip_id = np.full((rows, grid.width), -1, dtype=np.int16)
    for box in boxes:
        a, b = max(r0, box.r0), min(r1, box.r1)
        if a >= b or box.c0 >= box.c1:
            continue
        cols = np.arange(box.c0, box.c1)
        rr = np.arange(a, b)
        qx = np.broadcast_to(grid.col_x(cols)[None, :], (rr.size, cols.size))
        qy = np.broadcast_to(grid.row_y(rr)[:, None], (rr.size, cols.size))
        li, si, ok = box.geom.invert(qx.ravel(), qy.ravel())
        li = li.reshape(qx.shape)
        si = si.reshape(qx.shape)
        ok = ok.reshape(qx.shape)
        score = np.abs(si - box.geom.sensor.center_px).astype(np.float32)
        view = (slice(a - r0, b - r0), slice(box.c0, box.c1))
        take = ok & (score < best[view])
        if not take.any():
            continue
        li_n = np.clip(np.rint(li), 0, box.geom.n - 1).astype(np.int64) + box.geom.strip.line_start
        si_n = np.clip(np.rint(si), 0, box.geom.sensor.samples - 1).astype(np.int32)
        best[view] = np.where(take, score, best[view])
        line[view] = np.where(take, li_n, line[view])
        samp[view] = np.where(take, si_n, samp[view])
        strip_id[view] = np.where(take, box.geom.strip.index, strip_id[view])
    return line, samp, strip_id


def _read_lines(cube: np.memmap, lines: np.ndarray, band_idx: np.ndarray | None) -> np.ndarray:
    """按升序行号读成 (n, bands, samples)。相近的行合成一次连续读。"""
    if lines.size == 0:
        nb = cube.shape[1] if band_idx is None else band_idx.size
        return np.empty((0, nb, cube.shape[2]), dtype=cube.dtype)
    cuts = np.nonzero(np.diff(lines) > READ_GAP_LINES)[0] + 1
    parts: list[np.ndarray] = []
    for run in np.split(lines, cuts):
        a, b = int(run[0]), int(run[-1]) + 1
        chunk = cube[a:b] if band_idx is None else cube[a:b][:, band_idx, :]
        parts.append(np.asarray(chunk)[run - a])
    return np.concatenate(parts, axis=0)


def _gather(
    cube: np.memmap,
    band_idx: np.ndarray | None,
    line: np.ndarray,
    samp: np.ndarray,
    valid: np.ndarray,
    out: np.ndarray,
    max_cache_lines: int,
    stats: MosaicStats,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """把选中的原始像元填进 out。一块用到的原始行太多时按成果行再切小，原始行缓存不超预算。"""
    rows = out.shape[0]
    stack = [(0, rows)]
    while stack:
        if cancelled and cancelled():
            raise Cancelled()
        a, b = stack.pop()
        sub = valid[a:b]
        if not sub.any():
            continue
        rr, cc = np.nonzero(sub)
        src_lines = line[a:b][rr, cc]
        uniq = np.unique(src_lines)
        if uniq.size > max_cache_lines and b - a > 1:
            mid = (a + b) // 2
            stack.extend([(mid, b), (a, mid)])
            continue
        data = _read_lines(cube, uniq, band_idx)
        pos = np.searchsorted(uniq, src_lines)
        out[a + rr, :, cc] = data[pos, :, samp[a:b][rr, cc]]
        stats.source_lines_read += int(uniq.size)
        stats.valid_pixels += int(rr.size)


def rows_per_block(grid: OutputGrid, n_bands: int, samples: int, budget_bytes: int, overlap: float) -> int:
    """块越大读得越连续，但成果块和原始行缓存都要放进内存预算。"""
    out_row = grid.width * n_bands * 2
    # 每个成果行大约要读 overlap 条航带各一两行原始行
    src_row = max(1.0, overlap) * 1.5 * n_bands * samples * 2
    rows = int(budget_bytes / max(1.0, out_row + src_row))
    return max(8, min(rows, 2048, MAX_BLOCK_BYTES // max(1, out_row)))


def write_mosaic(
    cube: np.memmap,
    geoms: list[StripGeometry],
    grid: OutputGrid,
    out_path: Path,
    *,
    band_idx: np.ndarray | None,
    budget_bytes: int,
    on_block: Callable[[int, int, np.ndarray], None] | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> MosaicStats:
    """写 BIL 立方体到 out_path。每写完一块成果行 [r0, r1) 调一次 on_block(r0, r1, (行, 波段, 列))。"""
    nb = cube.shape[1] if band_idx is None else int(band_idx.size)
    samples = cube.shape[2]
    boxes = strip_boxes(geoms, grid)
    overlap = sum((b.c1 - b.c0) for b in boxes) / max(1, grid.width)
    block = rows_per_block(grid, nb, samples, budget_bytes, overlap)
    max_cache_lines = max(64, int(budget_bytes // 2 // (nb * samples * cube.dtype.itemsize)))
    stats = MosaicStats(strip_pixels={g.strip.index: 0 for g in geoms})
    log.info("mosaic %dx%d bands=%d block_rows=%d overlap=%.2f", grid.width, grid.height, nb, block, overlap)
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    try:
        with tmp.open("wb") as fh:
            for r0 in range(0, grid.height, block):
                if cancelled and cancelled():
                    raise Cancelled()
                r1 = min(grid.height, r0 + block)
                line, samp, strip_id = select_block(boxes, grid, r0, r1)
                valid = line >= 0
                out = np.full((r1 - r0, nb, grid.width), NODATA, dtype=cube.dtype)
                if valid.any():
                    _gather(cube, band_idx, line, samp, valid, out, max_cache_lines, stats, cancelled)
                    ids, counts = np.unique(strip_id[valid], return_counts=True)
                    for i, n in zip(ids.tolist(), counts.tolist()):
                        stats.strip_pixels[int(i)] = stats.strip_pixels.get(int(i), 0) + int(n)
                if on_block:
                    on_block(r0, r1, out)
                fh.write(out.tobytes(order="C"))
                stats.bytes_written += out.nbytes
                if progress:
                    progress(r1 / grid.height, f"第 {r1}/{grid.height} 行")
        tmp.replace(out_path)
    except BaseException:
        # 半截的立方体动辄几十 GB，不留在盘上
        tmp.unlink(missing_ok=True)
        raise
    return stats
