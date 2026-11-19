"""三波段预览：原始 DN 的 RGB GeoTIFF（逐块写，带金字塔），以及拉伸后的小 PNG。"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from pb_mosaic.geom.grid import OutputGrid
from pb_mosaic.ingest.envi import EnviHeader
from pb_mosaic.mosaic.merge import NODATA

PREVIEW_MAX_PX = 2048
STRETCH_PERCENT = (2.0, 98.0)
# 没有默认波段时按波长挑：红、绿、蓝
RGB_TARGET_NM = (640.0, 550.0, 460.0)
OVERVIEW_LEVELS = (2, 4, 8, 16, 32, 64)


def rgb_positions(hdr: EnviHeader, band_idx: np.ndarray | None) -> list[int]:
    """成果立方体里做 RGB 的三个波段位置（0 起始）。"""
    source = np.arange(hdr.bands) if band_idx is None else np.asarray(band_idx)
    n = int(source.size)
    if n < 3:
        return [0] * 3 if n == 1 else [0, n - 1, n - 1]
    pos = {int(b): k for k, b in enumerate(source)}
    wanted = [pos.get(b - 1) for b in hdr.default_bands[:3]]
    if len(wanted) == 3 and all(w is not None for w in wanted):
        return [int(w) for w in wanted]  # type: ignore[arg-type]
    if hdr.wavelengths:
        wl = np.asarray([hdr.wavelengths[int(b)] for b in source])
        return [int(np.argmin(np.abs(wl - t))) for t in RGB_TARGET_NM]
    return [n * 3 // 4, n // 2, n // 4]


class RgbSink:
    """接 write_mosaic 的 on_block：GeoTIFF 按行窗口写，PNG 用的缩略图同时抽样攒起来。"""

    def __init__(self, tif_path: Path, grid: OutputGrid, positions: list[int]) -> None:
        import rasterio
        from rasterio.transform import from_origin

        self.grid = grid
        self.positions = positions
        self.tif_path = tif_path
        self.tmp_path = tif_path.with_name(tif_path.name + ".part")
        self.step = max(1, math.ceil(max(grid.width, grid.height) / PREVIEW_MAX_PX))
        self.thumb = np.zeros(
            (math.ceil(grid.height / self.step), math.ceil(grid.width / self.step), 3), dtype=np.uint16
        )
        big = grid.width * grid.height * 6 > 3_500_000_000
        self.ds = rasterio.open(
            self.tmp_path,
            "w",
            driver="GTiff",
            width=grid.width,
            height=grid.height,
            count=3,
            dtype="uint16",
            crs=f"EPSG:{grid.epsg}",
            transform=from_origin(grid.x0, grid.y0, grid.gsd, grid.gsd),
            nodata=NODATA,
            compress="deflate",
            predictor=2,
            tiled=False,
            blockysize=16,
            photometric="RGB",
            BIGTIFF="YES" if big else "IF_SAFER",
        )

    def __call__(self, r0: int, r1: int, block: np.ndarray) -> None:
        from rasterio.windows import Window

        rgb = block[:, self.positions, :]  # (行, 3, 列)
        self.ds.write(np.transpose(rgb, (1, 0, 2)), window=Window(0, r0, self.grid.width, r1 - r0))
        first = (-r0) % self.step
        rows = np.arange(r0 + first, r1, self.step)
        if rows.size:
            sub = rgb[rows - r0][:, :, :: self.step]  # (n, 3, 列/step)
            self.thumb[rows // self.step] = np.transpose(sub, (0, 2, 1))

    def finish(self) -> None:
        from rasterio.enums import Resampling

        levels = [lv for lv in OVERVIEW_LEVELS if max(self.grid.width, self.grid.height) / lv >= 256]
        if levels:
            self.ds.build_overviews(levels, Resampling.nearest)
            self.ds.update_tags(ns="rio_overview", resampling="nearest")
        self.ds.close()
        self.tmp_path.replace(self.tif_path)

    def abort(self) -> None:
        try:
            self.ds.close()
        finally:
            self.tmp_path.unlink(missing_ok=True)


def stretch_to_png(thumb: np.ndarray, png_path: Path) -> dict:
    """每个通道在有效像元里按 2%–98% 拉到 0–255，无数据透明。返回拉伸参数。"""
    from PIL import Image

    valid = np.any(thumb != NODATA, axis=2)
    out = np.zeros(thumb.shape[:2] + (4,), dtype=np.uint8)
    ranges = []
    for c in range(3):
        ch = thumb[:, :, c].astype(np.float32)
        if valid.any():
            lo, hi = np.percentile(ch[valid], STRETCH_PERCENT)
        else:
            lo, hi = 0.0, 1.0
        hi = max(hi, lo + 1.0)
        out[:, :, c] = np.clip((ch - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
        ranges.append([float(lo), float(hi)])
    out[:, :, 3] = np.where(valid, 255, 0)
    tmp = png_path.with_name(png_path.name + ".part")
    Image.fromarray(out).save(tmp, format="PNG", optimize=True)
    tmp.replace(png_path)
    return {"stretch_dn": ranges, "width": int(out.shape[1]), "height": int(out.shape[0])}
