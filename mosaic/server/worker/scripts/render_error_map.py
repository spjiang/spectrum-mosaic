"""渲染 DSM 误差的空间分布图，用来看误差是一整片、一条带还是散点。

分箱统计只能说「某类格子平均差多少」，看不出形状。误差是成片偏低还是沿航线
条带分布，指向的原因完全不同：成片偏低多为基准/尺度，条带多为某几条航线的
姿态或匹配失败。所以必须出图。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

REF_DSM = Path("/data/input/MAX_20251017/拼图结果/DSM.tif")


def read(path: Path):
    with rasterio.open(path) as ds:
        z = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        return np.where(z < -1e6, np.nan, z), ds.transform, str(ds.crs), (ds.height, ds.width)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("--out", type=Path, default=Path("/data/output/_crop/err"))
    ap.add_argument("--max-px", type=int, default=1100)
    args = ap.parse_args()
    ours = args.ours / "DSM.tif" if args.ours.is_dir() else args.ours

    ref, tr, crs, shape = read(REF_DSM)
    z = np.full(shape, np.nan, np.float32)
    zo, to, co, _ = read(ours)
    reproject(
        zo, z, src_transform=to, src_crs=co, src_nodata=np.nan,
        dst_transform=tr, dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.bilinear,
    )
    d = z - ref
    step = max(1, int(np.ceil(max(shape) / args.max_px)))
    d = d[::step, ::step]
    ref_s, z_s = ref[::step, ::step], z[::step, ::step]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    args.out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(1, 3, figsize=(21, 7))
    lo, hi = np.nanpercentile(ref_s, [2, 98])
    ax[0].imshow(ref_s, cmap="gray", vmin=lo, vmax=hi)
    ax[0].set_title(f"commercial DSM [{lo:.0f},{hi:.0f}]")
    ax[1].imshow(z_s, cmap="gray", vmin=lo, vmax=hi)
    ax[1].set_title("ours (same stretch)")
    im = ax[2].imshow(d, cmap="RdBu_r", vmin=-20, vmax=20)
    ax[2].set_title("ours - commercial (m)")
    fig.colorbar(im, ax=ax[2], shrink=0.8)
    for a in ax:
        a.set_xticks([])
        a.set_yticks([])
    p = args.out / "dsm_error.png"
    fig.savefig(p, dpi=90, bbox_inches="tight")
    print(f"-> {p}  抽样步长={step}")
    ok = np.isfinite(d)
    print(f"中误差={np.nanstd(d[ok]):.2f} 偏差={np.nanmean(d[ok]):+.2f} |d|>20m 占 {np.mean(np.abs(d[ok])>20):.2%}")


if __name__ == "__main__":
    main()
