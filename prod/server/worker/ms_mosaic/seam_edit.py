"""把用户在预览图上圈定的范围栅格化到正射块上。

这些范围是 Boykov & Jolly, ICCV 2001 的硬约束：圈内来源影像锁死，
圈外仍由图割决定。图割轮数不因人工编辑而减少。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def load_seam_edits(path: Path | str, grid) -> list[tuple[np.ndarray, int]]:
    """GeoJSON（经纬度）转到格网投影坐标。每项是 (N×2 的平面坐标, 影像号)。"""
    from rasterio.warp import transform as warp_transform

    path = Path(path)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    edits: list[tuple[np.ndarray, int]] = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        try:
            image_id = int(props["image_id"])
        except (KeyError, TypeError, ValueError):
            continue
        geometry = feature.get("geometry") or {}
        polygons = []
        if geometry.get("type") == "Polygon":
            polygons = [geometry.get("coordinates") or []]
        elif geometry.get("type") == "MultiPolygon":
            polygons = list(geometry.get("coordinates") or [])
        for poly in polygons:
            if not poly:
                continue
            ring = np.asarray(poly[0], float)
            if ring.ndim != 2 or len(ring) < 3:
                continue
            xs, ys = warp_transform("EPSG:4326", grid.crs, ring[:, 0].tolist(), ring[:, 1].tolist())
            edits.append((np.column_stack([xs, ys]), image_id))
    return edits


def translate_edits(
    edits: list[tuple[np.ndarray, int]],
    image_to_shot: dict[int, int],
    shot_to_image: dict[int, int],
) -> list[tuple[np.ndarray, int]]:
    """彩色影像号按同一曝光换成当前波段的影像号。几何范围不变。"""
    out = []
    for xy, image_id in edits:
        shot = image_to_shot.get(int(image_id))
        if shot is None:
            continue
        mapped = shot_to_image.get(int(shot))
        if mapped is None:
            continue
        out.append((xy, int(mapped)))
    return out


def _hits_window(xy: np.ndarray, bounds: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bounds
    return not (
        float(xy[:, 0].max()) < minx
        or float(xy[:, 0].min()) > maxx
        or float(xy[:, 1].max()) < miny
        or float(xy[:, 1].min()) > maxy
    )


def rasterize_window(grid, window: tuple[int, int, int, int], edits: list[tuple[np.ndarray, int]]) -> np.ndarray | None:
    """窗口内的人工范围。-1 表示这块像素仍交给图割。没有命中时返回 None。"""
    from rasterio.features import rasterize
    from rasterio.transform import Affine

    if not edits:
        return None
    row0, col0, rows, cols = window
    transform = grid.transform * Affine.translation(col0, row0)
    minx, miny, maxx, maxy = (
        min(transform.c, transform.c + cols * transform.a),
        min(transform.f, transform.f + rows * transform.e),
        max(transform.c, transform.c + cols * transform.a),
        max(transform.f, transform.f + rows * transform.e),
    )
    shapes = []
    for xy, image_id in edits:
        if not _hits_window(xy, (minx, miny, maxx, maxy)):
            continue
        ring = [(float(x), float(y)) for x, y in xy]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        shapes.append(({"type": "Polygon", "coordinates": [ring]}, int(image_id)))
    if not shapes:
        return None
    burned = rasterize(shapes, out_shape=(rows, cols), transform=transform, fill=-1, dtype="int32")
    if int((burned >= 0).sum()) == 0:
        return None
    return burned


def ensure_ids_for(grid, window: tuple[int, int, int, int], edits: list[tuple[np.ndarray, int]]) -> tuple[int, ...]:
    """用户指定、并且范围碰到这块的影像。视角上限仍由 select_views 守住。"""
    row0, col0, rows, cols = window
    xs, ys = grid.cell_centers(window)
    minx, maxx = float(np.min(xs)), float(np.max(xs))
    miny, maxy = float(np.min(ys)), float(np.max(ys))
    ids = []
    for xy, image_id in edits:
        if _hits_window(xy, (minx, miny, maxx, maxy)):
            ids.append(int(image_id))
    return tuple(dict.fromkeys(ids))
