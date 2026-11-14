"""在指定地理坐标裁同一块地面，把自研与商业并排出 PNG，用来肉眼定性。

数值指标能说「差多少」，说不清「长什么样」。色块化、重影、糊，这三种毛病在
指标上都可能只表现为高频能量偏低，必须看图才能分清。裁图统一重采样到商业
格网，保证两边逐像元对同一块地面。
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


def read_window(path: Path, tr, crs, shape):
    """把 path 重采样到给定 transform/形状，返回 (3,h,w) uint8 与有效掩膜。"""
    with rasterio.open(path) as ds:
        n = min(3, ds.count)
        src = np.stack([ds.read(i + 1).astype(np.float32) for i in range(n)])
        if n == 1:
            src = np.repeat(src, 3, axis=0)
        alpha = (ds.read(4) > 0).astype(np.float32) if ds.count >= 4 else np.ones(src.shape[1:], np.float32)
        out = np.zeros((3,) + shape, np.float32)
        ao = np.zeros(shape, np.float32)
        for b in range(3):
            reproject(
                src[b], out[b], src_transform=ds.transform, src_crs=ds.crs,
                dst_transform=tr, dst_crs=crs, resampling=Resampling.bilinear,
            )
        reproject(
            alpha, ao, src_transform=ds.transform, src_crs=ds.crs,
            dst_transform=tr, dst_crs=crs, resampling=Resampling.nearest,
        )
    return out, ao > 0.5


def stretch(a: np.ndarray, ok: np.ndarray, lo_hi=None):
    v = a[:, ok]
    if v.size == 0:
        return np.zeros(a.shape, np.uint8)
    lo, hi = lo_hi or (np.percentile(v, 2), np.percentile(v, 98))
    s = np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1) * 255
    return np.where(ok[None], s, 0).astype(np.uint8), (lo, hi)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    ap.add_argument("x", type=float)
    ap.add_argument("y", type=float)
    ap.add_argument("--span", type=float, default=30.0, help="窗口边长（米）")
    ap.add_argument("--zoom", type=int, default=2, help="放大倍数（整数重复）")
    ap.add_argument("--out", type=Path, default=Path("/data/output/_crop"))
    ap.add_argument(
        "--tone",
        default=None,
        help="对自研先做仿射辐射归一化，格式 g,o;g,o;g,o（R;G;B），用全幅拟合值",
    )
    ap.add_argument(
        "--mode",
        choices=("raw", "shared", "each"),
        default="raw",
        help="raw 按原始 0-255（同 QGIS 默认）；shared 共用商业拉伸；each 各自拉伸，只比纹理",
    )
    args = ap.parse_args()
    ours = args.ours / ORTHO if args.ours.is_dir() else args.ours

    with rasterio.open(REF_DIR / ORTHO) as ds:
        gsd = float(ds.transform.a)
        crs = str(ds.crs)
    n = max(8, int(round(args.span / gsd)))
    from rasterio.transform import Affine

    tr = Affine(gsd, 0, args.x - args.span / 2, 0, -gsd, args.y + args.span / 2)
    shape = (n, n)

    args.out.mkdir(parents=True, exist_ok=True)
    co, cok = read_window(REF_DIR / ORTHO, tr, crs, shape)
    oo, ook = read_window(ours, tr, crs, shape)
    if args.tone:
        for b, part in enumerate(args.tone.split(";")[:3]):
            g, off = (float(v) for v in part.split(","))
            oo[b] = np.clip(oo[b] * g + off, 0, 255)
    both = cok & ook
    if args.mode == "raw":
        cimg = np.where(both[None], np.clip(co, 0, 255), 0).astype(np.uint8)
        oimg = np.where(both[None], np.clip(oo, 0, 255), 0).astype(np.uint8)
        lohi = (0.0, 255.0)
    elif args.mode == "each":
        cimg, lohi = stretch(co, both)
        oimg, _ = stretch(oo, both)
    else:
        cimg, lohi = stretch(co, both)
        oimg, _ = stretch(oo, both, lohi)

    from PIL import Image

    tag = f"{int(args.x)}_{int(args.y)}_{int(args.span)}m"
    for name, img in (("商业", cimg), ("自研", oimg)):
        im = Image.fromarray(np.moveaxis(img, 0, -1))
        if args.zoom > 1:
            im = im.resize((n * args.zoom, n * args.zoom), Image.NEAREST)
        p = args.out / f"{tag}_{name}.png"
        im.save(p)
        print(f"{name} -> {p}")
    print(f"窗口 {n}x{n} @ {gsd:.5f} m  拉伸区间={lohi[0]:.1f}~{lohi[1]:.1f} 共同有效={both.mean():.3f}")


if __name__ == "__main__":
    main()
