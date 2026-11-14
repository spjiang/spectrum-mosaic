"""像对筛选：用 POS 足迹重叠代替全组合匹配。

依据 GNSS/INS-Assisted SfM（Remote Sens. 2020, 12(3):351）：有 POS 时用轨迹先验
挑候选像对，比词袋检索更准也更快，长条带数据尤其明显。

668 曝光全组合是 22.3 万对；按足迹重叠取前 12 个邻居只剩约 8 千对，降低 28 倍，
这是把 10 km 条带压进可接受时间的前提。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ms_mosaic.camera import Camera, Pose, footprint

MAX_NEIGHBORS = 12
# 实测（MAX_20251017_001 前 40 曝光）各重叠区间的匹配成功率：
#   [0.1,0.3) 0%   [0.3,0.5) 0%   [0.5,0.7) 61%   [0.7,1.0) 100%
# 地形起伏 161 m、基线 68 m 的像对虽然按水平面算有 30~50% 足迹重叠，实际透视差过大
# 匹配不上。阈值定在 0.45 可砍掉约一半候选对而不损失任何连接点。
MIN_OVERLAP = 0.45


@dataclass(frozen=True)
class PairCandidate:
    i: int
    j: int
    overlap: float


def footprint_polygons(
    cameras: dict[int, Camera], poses: dict[int, Pose], ground_z: float
):
    """每帧地面足迹多边形，键为帧索引。"""
    from shapely.geometry import Polygon

    polys = {}
    for idx, pose in poses.items():
        cam = cameras[idx]
        poly = Polygon(footprint(cam, pose, ground_z))
        if poly.is_valid and poly.area > 0:
            polys[idx] = poly
    return polys


def overlap_ratio(poly_a, poly_b) -> float:
    """重叠面积占较小者的比例。用 min 而非 union，便于和航向/旁向重叠率口径对齐。"""
    inter = poly_a.intersection(poly_b).area
    if inter <= 0.0:
        return 0.0
    return float(inter / min(poly_a.area, poly_b.area))


def select_pairs(
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    ground_z: float,
    *,
    max_neighbors: int = MAX_NEIGHBORS,
    min_overlap: float = MIN_OVERLAP,
) -> list[PairCandidate]:
    """返回按重叠率排序的候选像对，i < j 且不重复。"""
    from shapely import STRtree

    polys = footprint_polygons(cameras, poses, ground_z)
    if len(polys) < 2:
        return []
    keys = sorted(polys)
    geoms = [polys[k] for k in keys]
    tree = STRtree(geoms)

    scored: dict[tuple[int, int], float] = {}
    for pos_i, key_i in enumerate(keys):
        hits = tree.query(geoms[pos_i])
        cand = []
        for pos_j in np.atleast_1d(hits):
            pos_j = int(pos_j)
            if pos_j == pos_i:
                continue
            ratio = overlap_ratio(geoms[pos_i], geoms[pos_j])
            if ratio >= min_overlap:
                cand.append((ratio, keys[pos_j]))
        cand.sort(reverse=True)
        for ratio, key_j in cand[:max_neighbors]:
            a, b = (key_i, key_j) if key_i < key_j else (key_j, key_i)
            prev = scored.get((a, b))
            if prev is None or ratio > prev:
                scored[(a, b)] = ratio

    out = [PairCandidate(a, b, r) for (a, b), r in scored.items()]
    out.sort(key=lambda p: -p.overlap)
    return out


def overlap_counts(
    cameras: dict[int, Camera], poses: dict[int, Pose], ground_z: float, transform, shape
) -> np.ndarray:
    """每个地面格网被多少张影像覆盖，用于报告里的重叠度视图。"""
    from rasterio.features import rasterize
    from shapely.geometry import Polygon

    counts = np.zeros(shape, np.uint16)
    for idx, pose in poses.items():
        poly = Polygon(footprint(cameras[idx], pose, ground_z))
        if not poly.is_valid or poly.area <= 0:
            continue
        mask = rasterize(
            [(poly, 1)], out_shape=shape, transform=transform, fill=0, dtype="uint8"
        )
        counts += mask
    return counts
