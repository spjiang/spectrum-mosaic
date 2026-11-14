"""找正射里的「平坦多边形面片」：最近邻填补留下的 Voronoi 色块。

真实影像几乎不存在 3×3 邻域完全同值的像元（除了过曝白斑）。而
inpaint_nearest 把空洞按最近有效像元填满，会生成一片片内部完全同值的
Voronoi 多边形 —— 放大看就是「低多边形」质感的色块，与周围细腻的冠层纹理
格格不入。统计同值连通块的占比和大小，就能判断这是不是问题来源；商业成品
作为对照。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from scipy.ndimage import label as cc_label

ORTHO = "Orthomosaic_pix_surf_group0.tif"
REF = Path("/data/input/MAX_20251017/拼图结果") / ORTHO


def load(path: Path):
    with rasterio.open(path) as ds:
        rgb = np.stack([ds.read(i + 1) for i in range(min(3, ds.count))]).astype(np.int32)
        alpha = ds.read(4) > 0 if ds.count >= 4 else np.ones(rgb.shape[1:], bool)
        tr = ds.transform
    return rgb, alpha, tr


def flat_mask(rgb: np.ndarray) -> np.ndarray:
    """与右邻和下邻都完全同值（三波段全等）的像元。"""
    same_r = np.zeros(rgb.shape[1:], bool)
    same_d = np.zeros(rgb.shape[1:], bool)
    same_r[:, :-1] = (rgb[:, :, :-1] == rgb[:, :, 1:]).all(axis=0)
    same_d[:-1, :] = (rgb[:, :-1, :] == rgb[:, 1:, :]).all(axis=0)
    return same_r & same_d


def report(name: str, path: Path, *, top: int = 0) -> None:
    rgb, alpha, tr = load(path)
    flat = flat_mask(rgb) & alpha
    n = int(alpha.sum())
    lab, n_cc = cc_label(flat)
    if not n_cc:
        print(f"{name}: 有效={n/alpha.size:.3f} 无同值团块")
        return
    sizes = np.bincount(lab.ravel())[1:]
    big = sizes[sizes >= 64]
    print(
        f"{name}: 有效={n/alpha.size:.3f} 同值像元={flat.sum()/max(n,1):.4%} "
        f"同值团块={n_cc} 其中≥64格={big.size} 最大团块={sizes.max()}格 "
        f"≥64格合计占有效={big.sum()/max(n,1):.4%}"
    )
    if top <= 0:
        return
    from scipy.ndimage import center_of_mass

    idx = (np.argsort(-sizes)[:top] + 1).tolist()
    for lbl, ctr in zip(idx, center_of_mass(flat, lab, idx)):
        x, y = tr * (ctr[1], ctr[0])
        print(f"    团块 {int(sizes[lbl-1])}格 像素=({int(ctr[1])},{int(ctr[0])}) 坐标=({x:.1f},{y:.1f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("--top", type=int, default=6, help="列出最大的若干同值团块及其坐标")
    args = ap.parse_args()
    p = args.ours / ORTHO if args.ours.is_dir() else args.ours
    report("自研", p, top=args.top)
    if REF.is_file():
        report("商业", REF, top=args.top)


if __name__ == "__main__":
    main()
