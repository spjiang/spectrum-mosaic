"""辐射链路核对：源影像 → 自研正射 → 商业正射，看亮度档位和饱和情况。

正射看起来「不像正常拼图」有两种完全不同的原因，指标上要分开：
  - 几何/接缝错位 → 局部重影、错断；
  - 辐射越界 → 亮处顶到 255 被截平，纹理连同层次一起消失。
后者在直方图上是右端堆积一根尖峰，这里直接量饱和比例与分位数，并和源 JPG
的分位数对照，判断放大是从哪一步进来的。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio

ORTHO = "Orthomosaic_pix_surf_group0.tif"
REF = Path("/data/input/MAX_20251017/拼图结果") / ORTHO


def stats(name: str, rgb: np.ndarray, ok: np.ndarray) -> None:
    n = max(int(ok.sum()), 1)
    parts = []
    for b, tag in enumerate("RGB"):
        v = rgb[b][ok].astype(np.float32)
        q = np.percentile(v, [1, 50, 99])
        parts.append(
            f"{tag} 均={v.mean():6.2f} p1/50/99={q[0]:5.1f}/{q[1]:5.1f}/{q[2]:5.1f} "
            f"饱和={np.mean(v >= 254.5):6.3%}"
        )
    print(f"{name}\n    " + "\n    ".join(parts) + f"\n    有效像元={n}")


def read_ortho(path: Path):
    with rasterio.open(path) as ds:
        rgb = np.stack([ds.read(i + 1) for i in range(3)])
        ok = ds.read(4) > 0 if ds.count >= 4 else np.ones(rgb.shape[1:], bool)
    return rgb, ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("--at", type=Path, default=None, help="at_result.npz，用来取源影像清单")
    ap.add_argument("--n-src", type=int, default=6)
    args = ap.parse_args()
    p = args.ours / ORTHO if args.ours.is_dir() else args.ours

    o, ook = read_ortho(p)
    stats("自研正射", o, ook)
    if REF.is_file():
        c, cok = read_ortho(REF)
        stats("商业正射", c, cok)

    if args.at is not None and args.at.is_file():
        from ms_mosaic.rawio import read_native

        with np.load(args.at, allow_pickle=False) as d:
            meta = json.loads(bytes(d["meta"]).decode("utf-8"))
        paths = [Path(s) for s in meta["image_paths"]]
        step = max(1, len(paths) // args.n_src)
        acc = []
        for path in paths[::step][: args.n_src]:
            arr = np.asarray(read_native(path))
            if arr.ndim == 3 and arr.shape[-1] >= 3:
                a = np.moveaxis(arr[..., :3], -1, 0)
                acc.append(a[:, ::8, ::8])
        if acc:
            src = np.concatenate(acc, axis=2)
            stats(f"源影像（{len(acc)} 张抽样）", src, np.ones(src.shape[1:], bool))


if __name__ == "__main__":
    main()
