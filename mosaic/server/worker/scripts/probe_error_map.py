"""把 DSM 误差按「视角重数」和「到边界距离」分箱，定位误差究竟集中在哪。

全幅中误差 16.9 m，而测区中心的小窗口只有 3.4 m，两者差了 5 倍 —— 说明误差
高度集中。分箱能区分两种完全不同的处置：
  - 误差集中在低重数/贴边的一圈 → 交付覆盖要收，是范围问题；
  - 误差在高重数的内部也一样大 → 是匹配或基准问题，收范围治不了。
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
from scipy.ndimage import distance_transform_edt

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.grid import Grid, ground_reference_z
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("cache", type=Path)
    args = ap.parse_args()
    ours = args.ours / "DSM.tif" if args.ours.is_dir() else args.ours

    with rasterio.open(ours) as ds:
        z = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
        grid = Grid(ds.transform, ds.width, ds.height, str(ds.crs))

    ref = np.full(grid.shape, np.nan, np.float32)
    with rasterio.open(REF_DSM) as ds:
        r = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            r = np.where(r == ds.nodata, np.nan, r)
        r = np.where(r < -1e6, np.nan, r)
        reproject(
            r, ref, src_transform=ds.transform, src_crs=ds.crs, src_nodata=np.nan,
            dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )

    cam, poses, pts, meta = load_at(args.cache)
    cams = {i: cam for i in poses}
    gz = ground_reference_z(pts)
    polys = footprint_polygons(cams, poses, gz)
    nviews = np.zeros(grid.shape, np.int16)
    for geom in polys.values():
        nviews += rasterize(
            [(geom, 1)], out_shape=grid.shape, transform=grid.transform,
            fill=0, dtype="uint8", all_touched=True,
        )

    ok = np.isfinite(z) & np.isfinite(ref)
    d = z - ref
    print(f"共同有效={ok.mean():.4f} 全幅 偏差={d[ok].mean():+.3f} 中误差={d[ok].std():.3f}")

    print(f"\n{'视角重数':>10} {'格数占比':>9} {'偏差':>8} {'中误差':>8} {'|d|p90':>8} {'>5m':>7}")
    edges = [1, 2, 3, 5, 8, 12, 16, 24, 51]
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = ok & (nviews >= lo) & (nviews < hi)
        if m.sum() < 100:
            continue
        v = d[m]
        print(
            f"{lo:>4}-{hi-1:<5} {m.mean():>9.4f} {v.mean():>+8.3f} {v.std():>8.3f} "
            f"{np.percentile(np.abs(v),90):>8.2f} {np.mean(np.abs(v)>5):>7.2%}"
        )

    # 按商业高程分箱：若误差随高程线性走，说明是垂直尺度/基准问题，
    # 而不是某几块匹配失败 —— 两者的处置完全不同。
    print(f"\n{'商业高程 m':>12} {'格数占比':>9} {'偏差':>8} {'中误差':>8} {'>5m':>7}")
    qs = np.nanpercentile(ref[ok], [0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100])
    for lo, hi in zip(qs[:-1], qs[1:]):
        m = ok & (ref >= lo) & (ref < hi)
        if m.sum() < 100:
            continue
        v = d[m]
        print(
            f"{lo:>6.0f}-{hi:<6.0f} {m.mean():>9.4f} {v.mean():>+8.3f} {v.std():>8.3f} "
            f"{np.mean(np.abs(v)>5):>7.2%}"
        )

    # 到有效域边界的距离（米），看误差是不是只贴边
    inside = distance_transform_edt(np.isfinite(z)) * grid.gsd
    print(f"\n{'距边界 m':>10} {'格数占比':>9} {'偏差':>8} {'中误差':>8} {'|d|p90':>8} {'>5m':>7}")
    for lo, hi in ((0, 10), (10, 25), (25, 50), (50, 100), (100, 1e9)):
        m = ok & (inside >= lo) & (inside < hi)
        if m.sum() < 100:
            continue
        v = d[m]
        print(
            f"{lo:>4}-{hi if hi < 1e8 else '∞':<5} {m.mean():>9.4f} {v.mean():>+8.3f} "
            f"{v.std():>8.3f} {np.percentile(np.abs(v),90):>8.2f} {np.mean(np.abs(v)>5):>7.2%}"
        )


if __name__ == "__main__":
    main()
