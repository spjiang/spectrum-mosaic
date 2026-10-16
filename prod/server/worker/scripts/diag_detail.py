"""量化「细节量」：DSM 局部起伏、正射高频能量，自研 vs 商业。

DSM 发白看不清 = 局部起伏（树冠颗粒）被磨掉；正射糊 = 高频能量低。
两者都在同一地理窗口、同一重采样尺度下比，避免 GSD 不同造成的假象。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy.ndimage import gaussian_filter, uniform_filter

REF = Path("/data/input/MAX_20251017/拼图结果")


def read_dsm(path: Path):
    with rasterio.open(path) as ds:
        z = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
        return z, ds.transform, ds.crs


def roughness(z: np.ndarray, win: int = 5) -> np.ndarray:
    """局部起伏：z 减去 win 窗口均值后的绝对值。nan 安全。"""
    ok = np.isfinite(z).astype(np.float32)
    zz = np.nan_to_num(z, nan=0.0)
    m = uniform_filter(zz, win, mode="nearest")
    d = uniform_filter(ok, win, mode="nearest")
    mean = np.where(d > 0.5, m / np.maximum(d, 1e-6), np.nan)
    return np.where(np.isfinite(z) & np.isfinite(mean), np.abs(z - mean), np.nan)


def dsm_detail(ours: Path) -> None:
    zc, tc, cc = read_dsm(REF / "DSM.tif")
    zo, to, co = read_dsm(ours / "DSM.tif")
    on_ref = np.full(zc.shape, np.nan, np.float32)
    reproject(
        zo, on_ref, src_transform=to, src_crs=co, src_nodata=np.nan,
        dst_transform=tc, dst_crs=cc, dst_nodata=np.nan, resampling=Resampling.bilinear,
    )
    both = np.isfinite(zc) & np.isfinite(on_ref)
    # 只在商业有效区的内部（腐蚀 60 格 ≈ 6.5 m）比较，排除边缘
    from scipy.ndimage import binary_erosion
    core = binary_erosion(both, np.ones((61, 61), bool))
    print(f"共同有效={both.mean():.3f} 内核={core.mean():.3f}")

    for win in (3, 5, 11, 31):
        rc = roughness(zc, win)
        ro = roughness(on_ref, win)
        print(
            f"窗口{win:>3}: 商业局部起伏 mean={np.nanmean(rc[core]):.3f} m  "
            f"自研 mean={np.nanmean(ro[core]):.3f} m  自研/商业={np.nanmean(ro[core]) / max(np.nanmean(rc[core]), 1e-9):.3f}"
        )

    d = on_ref - zc
    print(
        f"内核差值: mean={np.nanmean(d[core]):.3f} std={np.nanstd(d[core]):.3f} "
        f"p1={np.nanpercentile(d[core], 1):.2f} p50={np.nanpercentile(d[core], 50):.2f} "
        f"p99={np.nanpercentile(d[core], 99):.2f}"
    )
    print(f"内核起伏: 商业 std={np.nanstd(zc[core]):.2f} 自研 std={np.nanstd(on_ref[core]):.2f}")
    # 坑洞统计：比 31 窗口均值低 5 m 以上的孤立格
    ro31 = roughness(on_ref, 31)
    zz = np.nan_to_num(on_ref, nan=0.0)
    ok = np.isfinite(on_ref).astype(np.float32)
    m = uniform_filter(zz, 31, mode="nearest") / np.maximum(uniform_filter(ok, 31, mode="nearest"), 1e-6)
    pit = core & np.isfinite(on_ref) & ((on_ref - m) < -5.0)
    spike = core & np.isfinite(on_ref) & ((on_ref - m) > 5.0)
    print(f"坑洞(低于邻域31均值5m) 占内核 {pit.sum() / max(core.sum(), 1):.4%}；尖刺 {spike.sum() / max(core.sum(), 1):.4%}")
    del ro31


def ortho_detail(ours: Path) -> None:
    print("\n=== 正射高频能量（同一格网重采样后）===")
    with rasterio.open(REF / "Orthomosaic_pix_surf_group0.tif") as dc:
        tc, cc, wc, hc = dc.transform, dc.crs, dc.width, dc.height
        gc = dc.read(2).astype(np.float32)
        ac = dc.read(4)
    gc = np.where(ac > 0, gc, np.nan)
    with rasterio.open(ours / "Orthomosaic_pix_surf_group0.tif") as do:
        to, co = do.transform, do.crs
        go = do.read(2).astype(np.float32)
        ao = do.read(4)
    go = np.where(ao > 0, go, np.nan)
    on_ref = np.full(gc.shape, np.nan, np.float32)
    reproject(
        go, on_ref, src_transform=to, src_crs=co, src_nodata=np.nan,
        dst_transform=tc, dst_crs=cc, dst_nodata=np.nan, resampling=Resampling.bilinear,
    )
    both = np.isfinite(gc) & np.isfinite(on_ref)
    from scipy.ndimage import binary_erosion
    core = binary_erosion(both, np.ones((81, 81), bool))
    print(f"共同有效={both.mean():.3f} 内核={core.mean():.3f}")
    for s in (1.0, 2.0, 4.0):
        for name, arr in (("商业", gc), ("自研", on_ref)):
            hp = arr - gaussian_filter(np.nan_to_num(arr, nan=0.0), s, mode="nearest")
            print(f"  sigma={s}: {name} 高频 |hp| mean={np.nanmean(np.abs(hp[core])):.3f}")
    print(
        f"  绿波段均值 商业={np.nanmean(gc[core]):.2f} 自研={np.nanmean(on_ref[core]):.2f}；"
        f"std 商业={np.nanstd(gc[core]):.2f} 自研={np.nanstd(on_ref[core]):.2f}"
    )


if __name__ == "__main__":
    ours = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/output/runs/demo_max_20251017_dsmfix/拼图结果")
    dsm_detail(ours)
    ortho_detail(ours)
