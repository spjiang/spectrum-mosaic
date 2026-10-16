"""在边缘窗口上重做真正射，比较几种高程面：原 DSM / 贴边闭运算抬冠 / 常值平面。

西缘实测 DSM 比商业低 26 m，先验面同样低 25 m —— 空三点落在地面，商业是树冠。
真正射拿地面高去投树冠相片，相邻格从相片上挤在一起的像素取值，就是油彩波纹。
这里用同一套空三和外方位，只换高程面，看哪一种能把纹理拉回来。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.windows import from_bounds
from scipy.ndimage import distance_transform_edt

from ms_mosaic.camera import Camera
from ms_mosaic.grid import Grid
from ms_mosaic.ortho import NativeImageCache, OrthoConfig, best_view_mosaic, orthorectify_tile
from scripts.probe_dense import load_at

REF_RGB = Path("/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif")


def flatten_edge(z: np.ndarray, gsd: float, *, win_m: float = 40.0, band_m: float = 80.0) -> np.ndarray:
    """贴边一圈改用大窗口中值面，去掉错误起伏，等价于局部水平面纠正。"""
    from scipy.ndimage import median_filter

    ok = np.isfinite(z)
    if not ok.any():
        return z
    fill = np.where(ok, z, float(np.nanmedian(z)))
    size = max(3, int(round(win_m / max(gsd, 1e-6))) | 1)
    plane = median_filter(fill, size=size)
    dist = distance_transform_edt(ok) * gsd
    return np.where(ok & (dist <= band_m), plane, z)


def save_rgb(arr: np.ndarray, ok: np.ndarray, path: Path) -> None:
    rgb = np.clip(np.nan_to_num(arr[:3], nan=0.0), 0, 255).astype(np.uint8)
    rgb = np.where(ok[None], rgb, 0)
    Image.fromarray(np.moveaxis(rgb, 0, -1)).save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ours_dsm", type=Path)
    ap.add_argument("x", type=float)
    ap.add_argument("y", type=float)
    ap.add_argument("--span", type=float, default=40.0)
    ap.add_argument("--out", type=Path, default=Path("/data/output/_crop/edge_fix"))
    args = ap.parse_args()

    cam, poses, pts, paths, meta = load_at()
    cameras = {i: cam for i in poses}
    color_paths = {i: p for i, p in paths.items() if "Color" in p.name or p.suffix.lower() in {".jpg", ".jpeg"}}
    if not color_paths:
        color_paths = paths

    with rasterio.open(args.ours_dsm) as ds:
        w = from_bounds(args.x - args.span / 2, args.y - args.span / 2,
                        args.x + args.span / 2, args.y + args.span / 2, ds.transform)
        z = ds.read(1, window=w).astype(np.float64)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
        dsm_grid = Grid(ds.window_transform(w), z.shape[1], z.shape[0], str(ds.crs))

    with rasterio.open(REF_RGB) as ds:
        wr = from_bounds(args.x - args.span / 2, args.y - args.span / 2,
                         args.x + args.span / 2, args.y + args.span / 2, ds.transform)
        ref = np.stack([ds.read(i + 1, window=wr) for i in range(3)])
        ref_ok = ds.read(4, window=wr) > 0
        ortho_grid = Grid(ds.window_transform(wr), ref.shape[2], ref.shape[1], str(ds.crs))

    from ms_mosaic.dsm import resample_height

    images = NativeImageCache(color_paths)
    cfg = OrthoConfig()
    from scipy.ndimage import median_filter

    pad = np.where(np.isfinite(z), z, np.nanmedian(z))
    win = max(3, int(round(40.0 / max(dsm_grid.gsd, 1e-6))) | 1)
    variants = {
        "原DSM": z,
        "贴边中值40m": flatten_edge(z, dsm_grid.gsd, win_m=40.0, band_m=80.0),
        "常值中位面": np.where(np.isfinite(z), np.nanmedian(z), np.nan),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    save_rgb(ref, ref_ok, args.out / "商业.png")
    for name, zz in variants.items():
        z_ortho = resample_height(zz.astype(np.float32), dsm_grid, ortho_grid)
        stack = orthorectify_tile(
            ortho_grid, (0, 0, ortho_grid.height, ortho_grid.width),
            z_ortho, cameras, poses, images, cfg,
        )
        mosaic, _ = best_view_mosaic(stack)
        ok = np.isfinite(mosaic).all(axis=0)
        save_rgb(mosaic, ok, args.out / f"{name}.png")
        both = ok & ref_ok
        hf = lambda a: np.nanmean(np.abs(a - np.nanmean(a)))
        print(f"{name}: 有效={ok.mean():.3f} 共同={both.mean():.3f}")
    print(f"写出 {args.out}")


if __name__ == "__main__":
    main()
