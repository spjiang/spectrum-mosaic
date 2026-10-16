from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.merge import merge
from rasterio.transform import from_origin, xy as transform_xy
from rasterio.warp import Resampling, reproject

from ms_mosaic.catalog import Pos
from ms_mosaic.geo import frame_affine


def write_georef(src: Path, dst: Path, pos: Pos) -> dict[str, Any]:
    """把单帧 JPG/TIF 按 POS 旋到北向上 GeoTIFF。"""
    import warnings

    from rasterio.errors import NotGeoreferencedWarning

    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
    with rasterio.open(src, "r") as ds:
        data = ds.read()
        height, width = ds.height, ds.width
        count = ds.count
        dtype = np.dtype(ds.dtypes[0])
    src_affine, crs, gsd = frame_affine(pos, width, height)
    rows = [0, 0, height - 1, height - 1]
    cols = [0, width - 1, 0, width - 1]
    xs, ys = transform_xy(src_affine, rows, cols)
    west, east = float(min(xs)), float(max(xs))
    south, north = float(min(ys)), float(max(ys))
    dst_w = max(1, int(round((east - west) / gsd)))
    dst_h = max(1, int(round((north - south) / gsd)))
    dst_transform = from_origin(west, north, gsd, gsd)
    dest = np.zeros((count, dst_h, dst_w), dtype=dtype)
    crs_obj = CRS.from_string(crs)
    for band in range(count):
        reproject(
            source=data[band],
            destination=dest[band],
            src_transform=src_affine,
            src_crs=crs_obj,
            dst_transform=dst_transform,
            dst_crs=crs_obj,
            resampling=Resampling.bilinear,
            dst_nodata=0,
        )
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "height": dst_h,
        "width": dst_w,
        "count": count,
        "dtype": str(dtype),
        "crs": crs,
        "transform": dst_transform,
        "compress": "lzw",
    }
    if count >= 3:
        profile["photometric"] = "RGB"
    with rasterio.open(dst, "w", **profile) as out:
        out.write(dest)
    return {"gsd_m": gsd, "crs": crs, "shape": [dst_h, dst_w, count], "path": str(dst)}


def mosaic_paths(paths: list[Path], out_path: Path) -> dict[str, Any]:
    if not paths:
        raise ValueError("没有可镶嵌的影像")
    srcs = [rasterio.open(p) for p in paths]
    try:
        mosaic, transform = merge(srcs)
        profile = srcs[0].profile.copy()
        crs = srcs[0].crs
    finally:
        for src in srcs:
            src.close()
    count, height, width = mosaic.shape
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile.update(
        driver="GTiff",
        height=height,
        width=width,
        count=count,
        transform=transform,
        crs=crs,
        compress="lzw",
    )
    if width >= 256 and height >= 256:
        profile["tiled"] = True
        profile["blockxsize"] = 256
        profile["blockysize"] = 256
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mosaic)
    bounds = rasterio.transform.array_bounds(height, width, transform)
    return {
        "n_scenes": len(paths),
        "crs": str(crs),
        "bounds": [float(x) for x in bounds],
        "shape": [int(height), int(width), int(count)],
        "path": str(out_path.resolve()),
        "dtype": str(mosaic.dtype),
        "nonzero": int(np.count_nonzero(mosaic)),
    }
