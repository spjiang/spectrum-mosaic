"""量化正射的「块界伪影」：拼接是否在固定步长上留下痕迹。

思路：正射块按 ORTHO_TILE 步长切分，如果每块独立选拼接线、独立做多频段融合，
那么块界处会出现两种可见现象：
  1. 重影 —— 相邻块对同一地面格选了不同视角，羽化把两张不同的影像平均了；
  2. 亮度台阶 —— 块内融合的低频基准不一致。

两者都会让「列方向相邻差分的均值」在 step 的整数倍列上系统性偏高。把每列的
平均 |Δ| 按 col % step 分组求均值，正常拼接应该是平的；有块界伪影就会在
相位 0（以及 overlap 带）上鼓起来。商业成品作为「平的」的基准一起测。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio

REF = Path("/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif")


def load_gray(path: Path) -> np.ndarray:
    with rasterio.open(path) as ds:
        n = ds.count
        bands = [ds.read(i + 1).astype(np.float32) for i in range(min(3, n))]
        g = np.mean(bands, axis=0)
        if n >= 4:
            g = np.where(ds.read(4) > 0, g, np.nan)
    return g


def phase_profile(g: np.ndarray, step: int) -> tuple[np.ndarray, np.ndarray]:
    """返回 (按列相位的平均 |Δcol|, 按行相位的平均 |Δrow|)。"""
    out = []
    for axis in (1, 0):
        d = np.abs(np.diff(g, axis=axis))
        # 沿另一个轴求均值，得到每列/每行一个数
        prof = np.nanmean(d, axis=1 - axis)
        idx = np.arange(prof.size)
        acc = np.full(step, np.nan)
        for p in range(step):
            v = prof[idx % step == p]
            v = v[np.isfinite(v)]
            if v.size:
                acc[p] = v.mean()
        out.append(acc)
    return out[0], out[1]


def report(name: str, g: np.ndarray, step: int) -> None:
    col, row = phase_profile(g, step)
    for label, acc in (("列", col), ("行", row)):
        base = np.nanmedian(acc)
        worst = int(np.nanargmax(acc))
        # 相位 0 即块起始边界；overlap 带在相位 0 附近
        print(
            f"{name} {label}相位: 基线={base:.4f} "
            f"相位0={acc[0]:.4f}({acc[0] / base:.3f}×) "
            f"最大相位={worst} 值={acc[worst]:.4f}({acc[worst] / base:.3f}×)"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("--step", type=int, default=384)
    args = ap.parse_args()
    p = args.ours
    if p.is_dir():
        p = p / "Orthomosaic_pix_surf_group0.tif"
    print(f"步长={args.step}")
    report("自研", load_gray(p), args.step)
    if REF.is_file():
        report("商业", load_gray(REF), args.step)


if __name__ == "__main__":
    main()
