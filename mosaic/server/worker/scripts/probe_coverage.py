"""标定交付覆盖域：商业成果的外轮廓对应几重影像覆盖？

我们的覆盖取足迹并集（≥1 视角），于是测区最外圈只被一两张影像看到的地方
也进了交付。那里没有立体冗余，密集匹配解不出、只能靠最近邻填补 —— 正射是
Voronoi 色块、DSM 是疙瘩状噪声，整幅中误差被这一圈拉到 16 m。

商业成果的边界明显更收，且形状是光滑包络。这里把每格的视角重数算出来，
逐个门限与商业有效域做 IoU / 精度 / 召回对比，用实测定出门限，而不是拍脑袋。
多视立体需要 ≥3 视角才能做一致性检验（Hirschmüller SGM、COLMAP MVS 的
photometric consistency 都要求多于两视），所以 3 是先验最合理的候选。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.grid import (
    Grid,
    estimate_dsm_gsd,
    grid_from_footprints,
    ground_reference_z,
    smooth_coverage_mask,
)
from ms_mosaic.pairs import footprint_polygons

REF_DSM = Path("/data/input/MAX_20251017/拼图结果/DSM.tif")
_CAM = ("key", "width", "height", "f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2", "model")


def load_at(cache: Path):
    with np.load(cache, allow_pickle=False) as d:
        meta = json.loads(bytes(d["meta"]).decode("utf-8"))
        ids = [int(i) for i in d["pose_ids"]]
        pr, pc = np.asarray(d["pose_R"], float), np.asarray(d["pose_C"], float)
        pts = np.asarray(d["points"], float)
    poses = {i: Pose(rotation=pr[k], center=pc[k]) for k, i in enumerate(ids)}
    raw = {c["key"]: c for c in meta["cameras"]}
    cam = Camera(**{k: raw[meta.get("camera_key") or "Color"][k] for k in _CAM})
    return cam, poses, pts, meta


def view_count(grid: Grid, cams, poses, ground_z: float) -> np.ndarray:
    polys = footprint_polygons(cams, poses, ground_z)
    n = np.zeros(grid.shape, np.int16)
    for geom in polys.values():
        n += rasterize(
            [(geom, 1)], out_shape=grid.shape, transform=grid.transform,
            fill=0, dtype="uint8", all_touched=True,
        )
    return n


def ref_mask(grid: Grid) -> np.ndarray:
    out = np.zeros(grid.shape, np.uint8)
    with rasterio.open(REF_DSM) as ds:
        z = ds.read(1).astype(np.float32)
        ok = np.isfinite(z) & (z > -1e6)
        if ds.nodata is not None:
            ok &= z != ds.nodata
        reproject(
            ok.astype(np.uint8), out, src_transform=ds.transform, src_crs=ds.crs,
            dst_transform=grid.transform, dst_crs=grid.crs, resampling=Resampling.nearest,
        )
    return out > 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache", type=Path)
    args = ap.parse_args()
    cam, poses, pts, meta = load_at(args.cache)
    cams = {i: cam for i in poses}
    gz = ground_reference_z(pts)
    gsd = estimate_dsm_gsd(cam, poses, gz)
    grid = grid_from_footprints(cams, poses, gz, gsd, meta["crs"])
    print(f"格网 {grid.width}x{grid.height} @ {gsd:.6f} 参考面 z={gz:.2f}")

    n = view_count(grid, cams, poses, gz)
    ref = ref_mask(grid)
    print(f"商业有效格占本格网 {ref.mean():.4f}；视角重数 p50={np.median(n)} max={n.max()}")
    print(f"{'门限':>4} {'覆盖占比':>9} {'IoU':>7} {'精度':>7} {'召回':>7}")
    for k in range(1, 9):
        m = smooth_coverage_mask(n >= k, grid.gsd)
        inter = float((m & ref).sum())
        union = float((m | ref).sum())
        print(
            f"{k:>4} {m.mean():>9.4f} {inter/max(union,1):>7.4f} "
            f"{inter/max(float(m.sum()),1):>7.4f} {inter/max(float(ref.sum()),1):>7.4f}"
        )


if __name__ == "__main__":
    main()
