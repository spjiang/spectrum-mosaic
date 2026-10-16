"""诊断：把空三稀疏点与密集匹配结果分别对照商业 DSM，定位误差来源。

如果稀疏点已经偏离商业 DSM 好几米，问题在空三（基准/尺度/系统差）；
如果稀疏点吻合而密集结果不吻合，问题在密集匹配本身。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

from ms_mosaic.local_defaults import BENCHMARK, CACHE, INPUT, RUNS

COMMERCIAL_DSM = BENCHMARK / "DSM.tif"


def sample_raster(path: Path, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """在给定平面坐标处采样栅格（最近邻），越界或 nodata 返回 nan。"""
    with rasterio.open(path) as src:
        arr = src.read(1, masked=True).filled(np.nan).astype(np.float64)
        inv = ~src.transform
        cols, rows = inv * (xs, ys)
        cols = np.floor(np.asarray(cols)).astype(int)
        rows = np.floor(np.asarray(rows)).astype(int)
        ok = (cols >= 0) & (cols < src.width) & (rows >= 0) & (rows < src.height)
        out = np.full(xs.shape, np.nan)
        out[ok] = arr[rows[ok], cols[ok]]
    return out


def describe(name: str, diff: np.ndarray) -> None:
    ok = np.isfinite(diff)
    if ok.sum() < 10:
        print(f"{name}: 可比样本不足（{ok.sum()}）")
        return
    d = diff[ok]
    bias = float(np.mean(d))
    print(
        f"{name}: n={ok.sum()} 偏差均值={bias:+.3f} 中位={np.median(d):+.3f} "
        f"去偏中误差={np.sqrt(np.mean((d - bias) ** 2)):.3f} "
        f"P50|d|={np.percentile(np.abs(d - bias), 50):.3f} "
        f"P90|d|={np.percentile(np.abs(d - bias), 90):.3f} m"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=INPUT)
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--ours-dsm", type=Path, default=RUNS / "phase3" / "DSM.tif")
    ap.add_argument("--max-index", type=int, default=40)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    from ms_mosaic.runner import run_sparse

    sparse = run_sparse(
        args.input, args.cache, max_index=args.max_index, workers=args.workers, log=print
    )
    pts = sparse.points
    print(f"\n稀疏点 {len(pts)} 个")

    comm = sample_raster(COMMERCIAL_DSM, pts[:, 0], pts[:, 1])
    inside = np.isfinite(comm)
    print(f"落在商业 DSM 有效区内的稀疏点：{inside.sum()} / {len(pts)}")
    describe("稀疏点 − 商业DSM", pts[:, 2] - comm)

    # 只看轨迹长 >= 3 的点：交会更可靠，排除两片交会的弱点
    lengths = sparse.at.track_lengths if hasattr(sparse.at, "track_lengths") else None
    if lengths is not None and len(lengths) == len(pts):
        for min_len in (2, 3, 4):
            sel = lengths >= min_len
            describe(f"  轨迹长>={min_len} ({sel.sum()}点)", (pts[:, 2] - comm)[sel])

    # 用最小二乘拟合一个平面，看是否存在系统性倾斜（基准面不一致的典型表现）
    d = pts[:, 2] - comm
    ok = np.isfinite(d)
    if ok.sum() > 100:
        x = pts[ok, 0] - pts[ok, 0].mean()
        y = pts[ok, 1] - pts[ok, 1].mean()
        a = np.stack([np.ones_like(x), x, y], axis=1)
        coef, *_ = np.linalg.lstsq(a, d[ok], rcond=None)
        resid = d[ok] - a @ coef
        print(
            f"\n差值平面拟合：常数 {coef[0]:+.3f} m，"
            f"东向坡度 {coef[1] * 1000:+.3f} mm/m，北向坡度 {coef[2] * 1000:+.3f} mm/m"
        )
        print(f"  去掉平面后的残差中误差 {np.sqrt(np.mean(resid**2)):.3f} m")

    if args.ours_dsm.exists():
        ours = sample_raster(args.ours_dsm, pts[:, 0], pts[:, 1])
        print()
        describe("自研DSM − 商业DSM（在稀疏点处）", ours - comm)
        describe("自研DSM − 稀疏点（自洽性）", ours - pts[:, 2])


if __name__ == "__main__":
    main()
