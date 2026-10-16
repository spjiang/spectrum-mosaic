"""用修好的投影在几个边缘窗上重投，并排商业，确认油彩是否消失。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

from ms_mosaic.blend import multiband_blend
from ms_mosaic.checkpoint import load_at_payload
from ms_mosaic.dsm import flatten_edge_z, resample_height
from ms_mosaic.grid import Grid
from ms_mosaic.ortho import NativeImageCache, orthorectify_tile
from ms_mosaic.seamline import optimal_labels

AT = Path("/data/output/runs/demo_max_20251017_rgb/20260920_205435/cache/at_result.npz")
DSM = Path("/data/output/runs/demo_max_20251017_restore/拼图结果/DSM.tif")
REF = Path("/data/input/MAX_20251017/拼图结果")
OUT = Path("/data/output/_crop/edge_fix2")
WINDOWS = (
    ("右上北", 674607.2, 2620470.2, 40.0),
    ("右上南", 674601.6, 2620222.0, 40.0),
    ("东缘", 674600.0, 2620230.0, 50.0),
    ("北缘", 674260.0, 2620540.0, 50.0),
    ("内部对照", 674250.0, 2620200.0, 40.0),
)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = load_at_payload(AT)
    cam = next(iter(payload["camera_objs"].values()))
    poses = payload["poses"]
    cameras = {i: cam for i in poses}
    images = NativeImageCache(payload["image_path_map"], limit=16)
    ortho = Grid.from_raster(REF / "Orthomosaic_pix_surf_group0.tif")
    dsm_grid = Grid.from_raster(DSM)
    with rasterio.open(DSM) as ds:
        z = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
    z = flatten_edge_z(z, dsm_grid.gsd, win_m=40.0, band_m=80.0)
    for name, x, y, span in WINDOWS:
        n = max(8, int(round(span / ortho.gsd)))
        col = int(round((x - ortho.transform.c) / ortho.transform.a - n / 2))
        row = int(round((ortho.transform.f - y) / ortho.gsd - n / 2))
        col = int(np.clip(col, 0, ortho.width - n))
        row = int(np.clip(row, 0, ortho.height - n))
        window = (row, col, n, n)
        hz = resample_height(z, dsm_grid, ortho, window)
        stack = orthorectify_tile(ortho, window, hz, cameras, poses, images)
        if stack.pixels.shape[0] == 0:
            mosaic = np.zeros((3, n, n), np.float32)
        else:
            labels = optimal_labels(stack)
            mosaic = multiband_blend(stack, labels)
        rgb = np.clip(np.nan_to_num(mosaic[:3], nan=0.0), 0, 255).astype(np.uint8)
        with rasterio.open(REF / "Orthomosaic_pix_surf_group0.tif") as ds:
            ref = ds.read((1, 2, 3), window=rasterio.windows.Window(col, row, n, n))
            alpha = ds.read(4, window=rasterio.windows.Window(col, row, n, n)) > 0
        ref = np.where(alpha[None], ref, 0).astype(np.uint8)
        gap = np.full((3, n, 8), 255, np.uint8)
        pair = np.concatenate([rgb, gap, ref], axis=2)
        img = np.moveaxis(pair, 0, -1)
        img = np.repeat(np.repeat(img, 2, 0), 2, 1)
        Image.fromarray(img).save(OUT / f"{name}.png")
        covered = int(np.isfinite(mosaic[0]).sum()) if mosaic.size else 0
        print(f"{name} views={stack.pixels.shape[0]} covered={covered}/{n*n}", flush=True)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
