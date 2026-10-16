"""把自研与商业成品渲成同尺度 PNG，便于逐块目视核对。

输出到 /data/output/diag/ 下，只读成果。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import reproject

REF = Path("/data/input/MAX_20251017/拼图结果")
OUT = Path("/data/output/diag")


def read_dsm(path: Path):
    with rasterio.open(path) as ds:
        z = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
        return z, ds.transform, ds.crs


def grey(z: np.ndarray, lo: float, hi: float) -> Image.Image:
    v = (z - lo) / max(hi - lo, 1e-9)
    v = np.clip(v, 0.0, 1.0)
    rgb = np.zeros(z.shape + (3,), np.uint8)
    g = (v * 255).astype(np.uint8)
    for k in range(3):
        rgb[..., k] = g
    rgb[~np.isfinite(z)] = (255, 0, 0)
    return Image.fromarray(rgb)


def thumb(img: Image.Image, w: int = 1100) -> Image.Image:
    h = max(1, int(round(img.height * w / img.width)))
    return img.resize((w, h), Image.LANCZOS)


def dsm_pair() -> None:
    zc, tc, cc = read_dsm(REF / "DSM.tif")
    zo, to, co = read_dsm(OURS / "DSM.tif")
    both = np.concatenate([zc[np.isfinite(zc)], zo[np.isfinite(zo)]])
    lo, hi = float(np.percentile(both, 1)), float(np.percentile(both, 99))
    print(f"统一拉伸 {lo:.2f} ~ {hi:.2f}")
    thumb(grey(zc, lo, hi)).save(OUT / "dsm_商业.png")
    thumb(grey(zo, lo, hi)).save(OUT / "dsm_自研.png")

    # 自研重投到商业格网，出差值图
    ours_on_ref = np.full(zc.shape, np.nan, np.float32)
    reproject(
        zo,
        ours_on_ref,
        src_transform=to,
        src_crs=co,
        src_nodata=np.nan,
        dst_transform=tc,
        dst_crs=cc,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    d = ours_on_ref - zc
    ok = np.isfinite(d)
    print(
        f"差值 有效={ok.mean():.3f} mean={np.nanmean(d):.3f} std={np.nanstd(d):.3f} "
        f"p5={np.nanpercentile(d, 5):.2f} p50={np.nanpercentile(d, 50):.2f} "
        f"p95={np.nanpercentile(d, 95):.2f}"
    )
    amp = 15.0
    v = np.clip((d + amp) / (2 * amp), 0, 1)
    rgb = np.zeros(d.shape + (3,), np.uint8)
    rgb[..., 0] = (v * 255).astype(np.uint8)
    rgb[..., 2] = ((1 - v) * 255).astype(np.uint8)
    rgb[..., 1] = (255 * (1 - np.abs(v - 0.5) * 2)).astype(np.uint8)
    rgb[~ok] = 0
    thumb(Image.fromarray(rgb)).save(OUT / "dsm_差值_红高蓝低_15m.png")


def ortho_crop(cx: float, cy: float, half_m: float, tag: str) -> None:
    """在同一地理窗口裁两边正射，按各自 GSD 输出原始像元。"""
    for label, path in (("商业", REF / "Orthomosaic_pix_surf_group0.tif"), ("自研", OURS / "Orthomosaic_pix_surf_group0.tif")):
        with rasterio.open(path) as ds:
            inv = ~ds.transform
            c0, r0 = inv * (cx - half_m, cy + half_m)
            c1, r1 = inv * (cx + half_m, cy - half_m)
            c0, r0, c1, r1 = int(c0), int(r0), int(c1), int(r1)
            c0 = max(0, min(ds.width - 1, c0))
            c1 = max(c0 + 1, min(ds.width, c1))
            r0 = max(0, min(ds.height - 1, r0))
            r1 = max(r0 + 1, min(ds.height, r1))
            win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
            arr = ds.read((1, 2, 3), window=win)
        img = Image.fromarray(np.transpose(arr, (1, 2, 0)))
        img.save(OUT / f"ortho_{tag}_{label}.png")
        print(f"{tag} {label}: {img.size} gsd像元窗口 {c0},{r0} {c1 - c0}x{r1 - r0}")


def ortho_tile_probe(step: int = 384) -> None:
    """沿块边界统计灰度不连续，量化「方块」严重程度。"""
    with rasterio.open(OURS / "Orthomosaic_pix_surf_group0.tif") as ds:
        g = ds.read(1).astype(np.float32)
        a = ds.read(4)
    g = np.where(a > 0, g, np.nan)
    print(f"\n=== 块界不连续（自研，步长 {step}）===")
    for axis, name in ((1, "列方向"), (0, "行方向")):
        n = g.shape[axis]
        bnd = np.arange(step, n - 1, step)
        if bnd.size == 0:
            continue
        if axis == 1:
            jump = np.abs(g[:, bnd] - g[:, bnd - 1])
            base = np.abs(g[:, 1:] - g[:, :-1])
        else:
            jump = np.abs(g[bnd, :] - g[bnd - 1, :])
            base = np.abs(g[1:, :] - g[:-1, :])
        jm = float(np.nanmean(jump))
        bm = float(np.nanmean(base))
        print(f"{name}: 块界平均跳变={jm:.3f} 全图平均相邻差={bm:.3f} 比值={jm / max(bm, 1e-6):.3f}")


if __name__ == "__main__":
    OURS = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/output/runs/demo_max_20251017_dsmfix/拼图结果")
    OUT.mkdir(parents=True, exist_ok=True)
    dsm_pair()
    # 重叠区中心附近取几个窗口
    ortho_crop(674100.0, 2620300.0, 20.0, "中部")
    ortho_crop(674014.0, 2620450.0, 20.0, "北部")
    ortho_tile_probe()
