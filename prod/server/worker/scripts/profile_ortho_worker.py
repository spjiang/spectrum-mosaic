"""模拟一个正射子进程连续跑一段相邻块，记录常驻内存随块数的增长。

用法（worker 容器内）::

    python scripts/profile_ortho_worker.py dump --input <原始数据> --run <输出目录> --band 550nm --out /tmp/p.pkl
    python scripts/profile_ortho_worker.py run --payload /tmp/p.pkl --start 400 --n 80 [--trim]

run 在干净进程里只加载块计算需要的状态（与 compose._ortho_setup 相同），
排除检查点加载对 RSS 的干扰。
"""

from __future__ import annotations

import argparse
import pickle
import time
from pathlib import Path

import numpy as np


def _status() -> dict[str, int]:
    out = {}
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            key = line.split(":")[0]
            if key in ("VmRSS", "VmHWM", "RssAnon", "RssFile"):
                out[key] = int(line.split()[1]) // 1024
    return out


def dump(args) -> None:
    import rasterio

    from ms_mosaic.checkpoint import resolve_sparse
    from ms_mosaic.compose import _share_array
    from ms_mosaic.dsm import flatten_edge_z
    from ms_mosaic.grid import Grid
    from ms_mosaic.scene import transfer_band

    run = Path(args.run)
    sparse = resolve_sparse(Path(args.input), run / "cache", start_stage="S5_ortho", log=print)
    dsm_path = run / "拼图结果" / "DSM.tif"
    with rasterio.open(dsm_path) as ds:
        z = ds.read(1).astype(np.float32)
        nod = ds.nodata
    if nod is not None:
        z[z == nod] = np.nan
    dsm_grid = Grid.from_raster(dsm_path)
    cameras, poses, paths = transfer_band(sparse.block, sparse.at.cameras, sparse.at.poses, args.band)
    shared = _share_array(flatten_edge_z(z, dsm_grid.gsd))
    payload = {
        "grid": dsm_grid.refine(),
        "dsm_z_path": str(shared),
        "dsm_grid": dsm_grid,
        "cameras": cameras,
        "poses": poses,
        "paths": paths,
    }
    with open(args.out, "wb") as fh:
        pickle.dump(payload, fh)
    print(f"写出 {args.out}，DSM 共享 {shared}")


def run(args) -> None:
    from ms_mosaic.blend import multiband_blend
    from ms_mosaic.compose import ORTHO_OVERLAP, ORTHO_TILE, _ortho_fn, _ortho_setup
    from ms_mosaic.ortho import OrthoConfig
    from ms_mosaic.parallel import release_heap
    from ms_mosaic.seamline import SeamConfig

    del multiband_blend
    with open(args.payload, "rb") as fh:
        payload = pickle.load(fh)
    payload.update({"ortho_cfg": OrthoConfig(), "seam_cfg": SeamConfig(), "gains": {}})
    ctx = _ortho_setup(payload)
    windows = list(payload["grid"].tiles(ORTHO_TILE, overlap=ORTHO_OVERLAP))
    print("起始", _status(), flush=True)
    t0 = time.time()
    for i, w in enumerate(windows[args.start : args.start + args.n], 1):
        _ortho_fn(ctx, w)
        if args.trim:
            release_heap()
        if i % 10 == 0:
            print(f"{i:4d} 块 {time.time() - t0:6.1f}s", _status(), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--input", required=True)
    d.add_argument("--run", required=True)
    d.add_argument("--band", default="550nm")
    d.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--payload", required=True)
    r.add_argument("--start", type=int, default=400)
    r.add_argument("--n", type=int, default=80)
    r.add_argument("--trim", action="store_true")
    args = ap.parse_args()
    dump(args) if args.cmd == "dump" else run(args)


if __name__ == "__main__":
    main()
