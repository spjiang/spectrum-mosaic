"""查 DSM 高处被压低的原因：先验面能不能够到真实山顶。

密集匹配是在「先验面 ± 半宽」的高程带内做平面扫描。先验面由稀疏点插值而来，
如果山顶一带稀疏点稀缺，先验就是从低处外插过去的，真实高程落在搜索带之外，
解只能顶在带的上边界 —— 表现正是「最高的一成地面系统性偏低几十米」。

这里把三条线放在一起量：商业 DSM 的高程分布、稀疏点的高程分布、以及在高处
区域内先验面（稀疏点插值）与商业高程的差。若差值普遍超过搜索半宽，原因就定了。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.grid import Grid, estimate_z_margin_m, ground_reference_z

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
    ap.add_argument("cache", type=Path)
    args = ap.parse_args()
    cam, poses, pts, meta = load_at(args.cache)

    with rasterio.open(REF_DSM) as ds:
        ref = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            ref = np.where(ref == ds.nodata, np.nan, ref)
        ref = np.where(ref < -1e6, np.nan, ref)
        grid = Grid(ds.transform, ds.width, ds.height, str(ds.crs))

    okr = np.isfinite(ref)
    q = np.nanpercentile(ref[okr], [1, 50, 90, 99, 100])
    print(f"商业 DSM   p1/p50/p90/p99/max = {q[0]:.1f}/{q[1]:.1f}/{q[2]:.1f}/{q[3]:.1f}/{q[4]:.1f}")

    z = pts[:, 2]
    z = z[np.isfinite(z)]
    med = np.median(z)
    mad = 1.4826 * np.median(np.abs(z - med))
    z = z[np.abs(z - med) <= 8 * max(mad, 1.0)]
    qp = np.percentile(z, [1, 50, 90, 99, 100])
    print(f"稀疏点     p1/p50/p90/p99/max = {qp[0]:.1f}/{qp[1]:.1f}/{qp[2]:.1f}/{qp[3]:.1f}/{qp[4]:.1f}  n={z.size}")
    print(f"参考面 ground_reference_z = {ground_reference_z(pts):.2f}")
    print(f"搜索半宽 estimate_z_margin_m = {estimate_z_margin_m(pts):.2f} m")
    print(f"→ 稀疏点 max 比商业 p99 低 {qp[3 if False else 4] and (q[3] - qp[4]):+.1f} m")

    # 先验面：与 dense 相同的做法（稀疏点最近邻/线性插值），在商业格网上评估
    from scipy.interpolate import griddata

    step = max(1, int(round(2.0 / grid.gsd)))  # 约 2 m 抽样，够看趋势
    rr, cc = np.mgrid[0 : grid.height : step, 0 : grid.width : step]
    xs, ys = grid.cell_centers()
    xs, ys = xs[::step, ::step], ys[::step, ::step]
    prior = griddata(pts[:, :2], pts[:, 2], (xs, ys), method="linear")
    nn = griddata(pts[:, :2], pts[:, 2], (xs, ys), method="nearest")
    prior = np.where(np.isfinite(prior), prior, nn)
    r = ref[::step, ::step]
    ok = np.isfinite(r) & np.isfinite(prior)
    d = prior - r
    print(f"\n先验面 − 商业高程：全域 偏差={d[ok].mean():+.2f} 中误差={d[ok].std():.2f}")
    print(f"{'商业高程 m':>12} {'格数占比':>9} {'先验偏差':>9} {'|偏差|>8m':>9} {'|偏差|>16m':>10}")
    qs = np.nanpercentile(r[ok], [0, 50, 75, 87.5, 95, 100])
    for lo, hi in zip(qs[:-1], qs[1:]):
        m = ok & (r >= lo) & (r < hi)
        if m.sum() < 50:
            continue
        v = d[m]
        print(
            f"{lo:>6.0f}-{hi:<6.0f} {m.mean():>9.4f} {v.mean():>+9.2f} "
            f"{np.mean(np.abs(v)>8):>9.1%} {np.mean(np.abs(v)>16):>10.1%}"
        )


if __name__ == "__main__":
    main()
