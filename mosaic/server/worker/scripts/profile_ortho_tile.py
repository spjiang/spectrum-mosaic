"""实测真实数据上单块正射的内存峰值，用于标定 TILE_WORK_MB。

用法（worker 容器内）::

    python scripts/profile_ortho_tile.py --input <原始数据目录> --run <输出目录> [--band Color] [--n 40]

输出每块的 tracemalloc 峰值与分阶段占用，以及影像缓存占用。
"""

from __future__ import annotations

import argparse
import time
import tracemalloc
from pathlib import Path

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--band", default="Color")
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    import rasterio

    from ms_mosaic.checkpoint import resolve_sparse
    from ms_mosaic.compose import ORTHO_OVERLAP, ORTHO_TILE
    from ms_mosaic.dsm import flatten_edge_z, resample_height
    from ms_mosaic.grid import Grid
    from ms_mosaic.ortho import NativeImageCache, OrthoConfig, orthorectify_tile
    from ms_mosaic.parallel import CACHE_PER_WORKER
    from ms_mosaic.scene import transfer_band
    from ms_mosaic.seamline import SeamConfig, optimal_labels
    from ms_mosaic.blend import multiband_blend

    run = Path(args.run)
    sparse = resolve_sparse(Path(args.input), run / "cache", start_stage="S5_ortho", log=print)
    dsm_path = run / "拼图结果" / "DSM.tif"
    with rasterio.open(dsm_path) as ds:
        z = ds.read(1).astype(np.float32)
        nod = ds.nodata
    if nod is not None:
        z[z == nod] = np.nan
    dsm_grid = Grid.from_raster(dsm_path)
    ortho_grid = dsm_grid.refine()
    ortho_z = flatten_edge_z(z, dsm_grid.gsd)
    cameras, poses, paths = transfer_band(sparse.block, sparse.at.cameras, sparse.at.poses, args.band)
    images = NativeImageCache(paths, limit=CACHE_PER_WORKER)
    windows = list(ortho_grid.tiles(ORTHO_TILE, overlap=ORTHO_OVERLAP))
    pick = np.linspace(0, len(windows) - 1, args.n).astype(int)
    cfg, seam_cfg = OrthoConfig(), SeamConfig()
    print(f"格网 {ortho_grid.width}×{ortho_grid.height}，{len(windows)} 块，抽 {len(pick)} 块")

    rows = []
    for i in pick:
        w = windows[i]
        tracemalloc.start()
        t0 = time.time()
        zt = resample_height(ortho_z, dsm_grid, ortho_grid, w)
        stack = orthorectify_tile(ortho_grid, w, zt, cameras, poses, images, cfg)
        _, p_stack = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        if stack.pixels.shape[0]:
            labels = optimal_labels(stack, seam_cfg)
            _, p_seam = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            multiband_blend(stack, labels)
            _, p_blend = tracemalloc.get_traced_memory()
        else:
            p_seam = p_blend = 0
        tracemalloc.stop()
        n_img = len(images._cache)
        cache_mb = sum(a.nbytes for a in images._cache.values()) / 2**20
        rows.append((i, len(stack.views), p_stack, p_seam, p_blend, cache_mb, time.time() - t0))
        print(
            f"块 {i:5d} 视角 {len(stack.views):2d}  重采样峰值 {p_stack / 2**20:7.1f} MB  "
            f"图割峰值 {p_seam / 2**20:7.1f} MB  融合峰值 {p_blend / 2**20:7.1f} MB  "
            f"缓存 {n_img} 张 {cache_mb:6.1f} MB  {time.time() - t0:5.1f}s",
            flush=True,
        )
    arr = np.array([r[2:5] for r in rows]) / 2**20
    print("最大：重采样 %.1f MB，图割 %.1f MB，融合 %.1f MB" % tuple(arr.max(axis=0)))


if __name__ == "__main__":
    main()
