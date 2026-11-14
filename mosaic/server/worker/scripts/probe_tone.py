"""量出商业正射相对自研的辐射传递曲线，判断它到底做了什么。

自研输出忠实于源 JPG（G 均值 114.3 vs 源 107.4，饱和 0.18% vs 0.16%），商业
明显更暗且完全不饱和（G 均值 91.1，p99=196，饱和 0）。差别不是随机的，一定是
一条确定的映射。把两边在同一地理格网上做分位数匹配，就能把这条曲线量出来：
  - 若是直线过原点 → 只是一个曝光系数；
  - 若是幂函数 → 伽马；
  - 若低端斜率 >1、高端 <1 → 压高光的 S 形（商业常用的色调映射）。
同时给出各候选模型的拟合残差，用数据选，而不是猜。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

ORTHO = "Orthomosaic_pix_surf_group0.tif"
REF_DIR = Path("/data/input/MAX_20251017/拼图结果")


def on_ref(path: Path, tr, crs, shape):
    with rasterio.open(path) as ds:
        out = np.zeros((3,) + shape, np.float32)
        for b in range(3):
            src = ds.read(b + 1).astype(np.float32)
            if (ds.transform, str(ds.crs), (ds.height, ds.width)) == (tr, crs, shape):
                out[b] = src
            else:
                reproject(
                    src, out[b], src_transform=ds.transform, src_crs=ds.crs,
                    dst_transform=tr, dst_crs=crs, resampling=Resampling.bilinear,
                )
        ok = np.ones(shape, bool)
        if ds.count >= 4:
            a = np.zeros(shape, np.float32)
            reproject(
                (ds.read(4) > 0).astype(np.float32), a,
                src_transform=ds.transform, src_crs=ds.crs,
                dst_transform=tr, dst_crs=crs, resampling=Resampling.nearest,
            )
            ok = a > 0.5
    return out, ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("--step", type=int, default=7, help="抽样步长，避免整幅驻留")
    args = ap.parse_args()
    ours = args.ours / ORTHO if args.ours.is_dir() else args.ours

    with rasterio.open(REF_DIR / ORTHO) as ds:
        tr, crs, shape = ds.transform, str(ds.crs), (ds.height, ds.width)
    c, cok = on_ref(REF_DIR / ORTHO, tr, crs, shape)
    o, ook = on_ref(ours, tr, crs, shape)
    m = cok & ook
    s = args.step
    c, o, m = c[:, ::s, ::s], o[:, ::s, ::s], m[::s, ::s]
    print(f"配对像元 {int(m.sum())}")

    qs = np.arange(1, 100)
    for b, tag in enumerate("RGB"):
        x = o[b][m]
        y = c[b][m]
        qx = np.percentile(x, qs)
        qy = np.percentile(y, qs)
        # 候选一：过原点线性
        k = float((qx * qy).sum() / max((qx * qx).sum(), 1e-9))
        r_lin = float(np.sqrt(np.mean((k * qx - qy) ** 2)))
        # 候选二：伽马 y = 255*(x/255)^g，按对数域最小二乘
        ok = (qx > 2) & (qy > 2)
        g = float(np.sum(np.log(qy[ok] / 255) * np.log(qx[ok] / 255)) / max(np.sum(np.log(qx[ok] / 255) ** 2), 1e-9))
        r_gam = float(np.sqrt(np.mean((255 * (qx / 255) ** g - qy) ** 2)))
        # 候选三：线性 + 截距
        A = np.vstack([qx, np.ones_like(qx)]).T
        (a1, a0), *_ = np.linalg.lstsq(A, qy, rcond=None)
        r_aff = float(np.sqrt(np.mean((a1 * qx + a0 - qy) ** 2)))
        print(
            f"\n{tag}: 过原点线性 k={k:.4f} 残差={r_lin:.2f} | "
            f"伽马 g={g:.4f} 残差={r_gam:.2f} | 仿射 {a1:.4f}x{a0:+.2f} 残差={r_aff:.2f}"
        )
        print("    分位  自研→商业  局部斜率")
        for q in (5, 15, 30, 50, 70, 85, 95, 99):
            i = q - 1
            j = min(i + 5, 98)
            slope = (qy[j] - qy[i]) / max(qx[j] - qx[i], 1e-6)
            print(f"    p{q:<3} {qx[i]:6.1f}→{qy[i]:6.1f}  {slope:6.3f}")


if __name__ == "__main__":
    main()
