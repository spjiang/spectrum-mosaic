from __future__ import annotations

import math
from collections.abc import Mapping

from rasterio.transform import Affine
from rasterio.warp import transform as warp_transform

from ms_mosaic.catalog import Pos, Shot

CALIB_WIDTH_PX = 1440.0
CALIB_FOCAL_PX = 1508.19  # LiMapper MAX-S800 RGB 组，按宽度缩放到实图
MIN_AGL_M = 5.0


def epsg_utm(lon: float, lat: float) -> str:
    zone = int((lon + 180.0) // 6) + 1
    hemi = 32600 if lat >= 0 else 32700
    return f"EPSG:{hemi + zone}"


def lonlat_to_utm(lon: float, lat: float, crs: str) -> tuple[float, float]:
    xs, ys = warp_transform("EPSG:4326", crs, [lon], [lat])
    return float(xs[0]), float(ys[0])


def gsd_m(agl_m: float, width: int, focal_px_at_calib: float = CALIB_FOCAL_PX) -> float:
    f_px = focal_px_at_calib * (float(width) / CALIB_WIDTH_PX)
    return float(agl_m) / max(f_px, 1e-6)


def filter_usable(shots: Mapping[int, Shot], min_agl_m: float = MIN_AGL_M) -> dict[int, Shot]:
    keep: dict[int, Shot] = {}
    for idx, shot in shots.items():
        if shot.pos is None:
            continue
        if shot.role == "W":
            continue
        if shot.pos.agl_m < min_agl_m:
            continue
        keep[idx] = shot
    return keep


def frame_affine(
    pos: Pos,
    width: int,
    height: int,
    *,
    gsd: float | None = None,
    crs: str | None = None,
) -> tuple[Affine, str, float]:
    crs = crs or epsg_utm(pos.lon, pos.lat)
    gsd = float(gsd if gsd is not None else gsd_m(pos.agl_m, width))
    east, north = lonlat_to_utm(pos.lon, pos.lat, crs)
    yaw = math.radians(pos.yaw_deg)
    cy = (height - 1) / 2.0
    cx = (width - 1) / 2.0
    cos_y = math.cos(yaw)
    sin_y = math.sin(yaw)
    a = gsd * cos_y
    b = gsd * (-sin_y)
    d = gsd * (-sin_y)
    e = gsd * (-cos_y)
    c = east - a * cx - b * cy
    f = north - d * cx - e * cy
    return Affine(a, b, c, d, e, f), crs, gsd
