"""定位正射最差的区域，输出地理坐标，供小窗口验证台复现。

用「同一地理格网下，自研高频能量 / 商业高频能量」做打分：比值远小于 1 的
地方就是纹理被抹平（糊、色块化），远大于 1 的地方是噪声或重影。按 64×64 的
粗块统计，排出最差的若干块并给出中心坐标。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy.ndimage import gaussian_filter, uniform_filter

ORTHO = "Orthomosaic_pix_surf_group0.tif"
REF_DIR = Path("/data/input/MAX_20251017/拼图结果")


def gray_on_ref(path: Path, ref):
    tc, cc, shape = ref
    with rasterio.open(path) as ds:
        g = np.mean([ds.read(i + 1).astype(np.float32) for i in range(min(3, ds.count))], axis=0)
        if ds.count >= 4:
            g = np.where(ds.read(4) > 0, g, np.nan)
        if (ds.transform, str(ds.crs), (ds.height, ds.width)) == (tc, cc, shape):
            return g
        out = np.full(shape, np.nan, np.float32)
        reproject(
            g, out, src_transform=ds.transform, src_crs=ds.crs, src_nodata=np.nan,
            dst_transform=tc, dst_crs=cc, dst_nodata=np.nan, resampling=Resampling.bilinear,
        )
        return out


def hf(g: np.ndarray, sigma: float = 2.0) -> np.ndarray:
    """高频幅度 |g − 高斯低通(g)|，nan 处置 nan。"""
    ok = np.isfinite(g)
    zz = np.nan_to_num(g, nan=0.0)
    lo = gaussian_filter(zz, sigma, mode="nearest")
    d = gaussian_filter(ok.astype(np.float32), sigma, mode="nearest")
    lo = np.where(d > 0.3, lo / np.maximum(d, 1e-6), np.nan)
    return np.where(ok & np.isfinite(lo), np.abs(g - lo), np.nan)


def block_mean(a: np.ndarray, blk: int) -> np.ndarray:
    ok = np.isfinite(a).astype(np.float32)
    s = uniform_filter(np.nan_to_num(a, nan=0.0), blk, mode="constant")[blk // 2::blk, blk // 2::blk]
    c = uniform_filter(ok, blk, mode="constant")[blk // 2::blk, blk // 2::blk]
    return np.where(c > 0.9, s / np.maximum(c, 1e-6), np.nan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("--blk", type=int, default=64)
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()
    ours = args.ours / ORTHO if args.ours.is_dir() else args.ours

    with rasterio.open(REF_DIR / ORTHO) as ds:
        ref = (ds.transform, str(ds.crs), (ds.height, ds.width))
    gc = gray_on_ref(REF_DIR / ORTHO, ref)
    go = gray_on_ref(ours, ref)
    both = np.isfinite(gc) & np.isfinite(go)
    gc = np.where(both, gc, np.nan)
    go = np.where(both, go, np.nan)

    bc = block_mean(hf(gc), args.blk)
    bo = block_mean(hf(go), args.blk)
    ratio = bo / np.maximum(bc, 1e-6)
    ratio = np.where(np.isfinite(bc) & np.isfinite(bo) & (bc > 1.0), ratio, np.nan)
    print(f"块数={np.isfinite(ratio).sum()} 比值 p05/p50/p95="
          f"{np.nanpercentile(ratio,5):.3f}/{np.nanpercentile(ratio,50):.3f}/{np.nanpercentile(ratio,95):.3f}")

    tr, _, _ = ref
    flat = ratio.ravel()
    order = np.argsort(np.where(np.isfinite(flat), flat, np.inf))
    print(f"\n最糊的 {args.top} 块（自研/商业 高频比值最低）：")
    for k in order[: args.top]:
        r, c = np.unravel_index(k, ratio.shape)
        px, py = (c * args.blk + args.blk / 2), (r * args.blk + args.blk / 2)
        x, y = tr * (px, py)
        print(f"  比值={flat[k]:.3f} 像素=({int(px)},{int(py)}) 坐标=({x:.1f},{y:.1f})")
    print(f"\n最噪的 {args.top} 块（比值最高，重影/噪声）：")
    for k in order[::-1][: args.top]:
        if not np.isfinite(flat[k]):
            continue
        r, c = np.unravel_index(k, ratio.shape)
        px, py = (c * args.blk + args.blk / 2), (r * args.blk + args.blk / 2)
        x, y = tr * (px, py)
        print(f"  比值={flat[k]:.3f} 像素=({int(px)},{int(py)}) 坐标=({x:.1f},{y:.1f})")


if __name__ == "__main__":
    main()
