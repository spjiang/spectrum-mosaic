"""核对边缘「油彩波纹」是不是 DSM 错高程导致的真正射拉伸。

真正射对每个地面格 (X,Y,Z_dsm) 反投到相片采样。Z 错了，相邻格就会从
相片上挤在一起的一小撮像素里取值，树冠被拉成波纹。这里量三件事：
  1. 该窗口 DSM 相对商业的偏差/中误差；
  2. 正射高频能量比（波纹区高频会异常低或异常「光滑」）；
  3. 商业 DSM 的局部坡度 vs 自研，看是不是填洞面把坡度抹平或拧歪。
"""

from __future__ import annotations

import argparse

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from scipy.ndimage import gaussian_filter


def win(ds, x, y, span):
    w = from_bounds(x - span / 2, y - span / 2, x + span / 2, y + span / 2, ds.transform)
    return ds.read(1, window=w).astype(np.float32), w


def hf(a, sigma=2.0):
    ok = np.isfinite(a)
    z = np.nan_to_num(a, nan=0.0)
    lo = gaussian_filter(z, sigma, mode="nearest")
    d = gaussian_filter(ok.astype(np.float32), sigma, mode="nearest")
    lo = np.where(d > 0.3, lo / np.maximum(d, 1e-6), np.nan)
    return np.where(ok & np.isfinite(lo), np.abs(a - lo), np.nan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours_dir")
    ap.add_argument("x", type=float)
    ap.add_argument("y", type=float)
    ap.add_argument("--span", type=float, default=40.0)
    args = ap.parse_args()
    ours_dsm = f"{args.ours_dir}/DSM.tif"
    ours_rgb = f"{args.ours_dir}/Orthomosaic_pix_surf_group0.tif"
    ref_dsm = "/data/input/MAX_20251017/拼图结果/DSM.tif"
    ref_rgb = "/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif"

    with rasterio.open(ours_dsm) as a, rasterio.open(ref_dsm) as b:
        zo, _ = win(a, args.x, args.y, args.span)
        zr, _ = win(b, args.x, args.y, args.span)
        if a.nodata is not None:
            zo = np.where(zo == a.nodata, np.nan, zo)
        if b.nodata is not None:
            zr = np.where(zr == b.nodata, np.nan, zr)
    zo = np.where(zo < -1e6, np.nan, zo)
    zr = np.where(zr < -1e6, np.nan, zr)
    ok = np.isfinite(zo) & np.isfinite(zr)
    print(f"窗口 ({args.x:.1f},{args.y:.1f}) {args.span:.0f}m  DSM 共同有效={ok.mean():.3f}")
    if ok.any():
        d = zo[ok] - zr[ok]
        gy, gx = np.gradient(np.nan_to_num(zo, nan=float(np.nanmedian(zo))))
        hy, hx = np.gradient(np.nan_to_num(zr, nan=float(np.nanmedian(zr))))
        print(
            f"  DSM 偏差={d.mean():+.2f} 中误差={d.std():.2f} |d|p90={np.percentile(np.abs(d),90):.2f} "
            f"自研坡度std={np.hypot(gx,gy)[ok].std():.3f} 商业={np.hypot(hx,hy)[ok].std():.3f}"
        )

    with rasterio.open(ours_rgb) as a, rasterio.open(ref_rgb) as b:
        go = np.mean([a.read(i + 1, window=from_bounds(
            args.x - args.span / 2, args.y - args.span / 2,
            args.x + args.span / 2, args.y + args.span / 2, a.transform)).astype(np.float32)
            for i in range(3)], axis=0)
        gr = np.mean([b.read(i + 1, window=from_bounds(
            args.x - args.span / 2, args.y - args.span / 2,
            args.x + args.span / 2, args.y + args.span / 2, b.transform)).astype(np.float32)
            for i in range(3)], axis=0)
        ao = a.read(4, window=from_bounds(
            args.x - args.span / 2, args.y - args.span / 2,
            args.x + args.span / 2, args.y + args.span / 2, a.transform)) > 0
        ar = b.read(4, window=from_bounds(
            args.x - args.span / 2, args.y - args.span / 2,
            args.x + args.span / 2, args.y + args.span / 2, b.transform)) > 0
    both = ao & ar
    ho, hr = hf(np.where(ao, go, np.nan)), hf(np.where(ar, gr, np.nan))
    print(
        f"  正射高频 自研={np.nanmean(ho[both]):.2f} 商业={np.nanmean(hr[both]):.2f} "
        f"比={np.nanmean(ho[both])/max(np.nanmean(hr[both]),1e-6):.3f} 共同覆盖={both.mean():.3f}"
    )


if __name__ == "__main__":
    main()
