"""Phase 3：空三 → 密集匹配 → DSM，并与商业 DSM.tif 逐项比对。

    python scripts/phase3_dsm.py --max-index 60 --dsm-gsd 0.107747293
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from ms_mosaic.compare import compare_dsm
from ms_mosaic.dense import DenseConfig, compute_height_field
from ms_mosaic.dsm import build_dsm, write_geotiff
from ms_mosaic.grid import Grid, grid_from_points
from ms_mosaic.local_defaults import BENCHMARK, CACHE, INPUT, RUNS
from ms_mosaic.runner import run_sparse

DEFAULT_INPUT = INPUT
COMMERCIAL_DSM = BENCHMARK / "DSM.tif"
COMMERCIAL_DSM_GSD = 0.107747293


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=RUNS / "phase3")
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--max-index", type=int, default=60)
    ap.add_argument("--dsm-gsd", type=float, default=COMMERCIAL_DSM_GSD)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--layers", type=int, default=48)
    ap.add_argument("--z-margin", type=float, default=24.0)
    ap.add_argument("--max-views", type=int, default=10)
    ap.add_argument("--tile", type=int, default=384)
    ap.add_argument("--pyramid", type=int, default=2, help="由粗到细的层级数")
    ap.add_argument("--no-antialias", action="store_true", help="消融实验：关掉采样预平滑")
    ap.add_argument("--clip-to-commercial", action="store_true",
                    help="把格网裁到商业 DSM 范围内，便于同范围比对")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    print("=== 稀疏重建 ===")
    sparse = run_sparse(
        args.input, args.cache, max_index=args.max_index, workers=args.workers, log=print
    )
    s = sparse.at.stats
    print(f"RMS 重投影 {s['rms_reprojection_px']:.4f} px（商业 0.410198）")
    print(f"GPS RMSE   {s['gps_rmse_m']:.3f} m（商业 6.2806）")

    pts = sparse.points
    print(f"稀疏点 {len(pts)} 个，高程 {pts[:, 2].min():.1f} / "
          f"{np.median(pts[:, 2]):.1f} / {pts[:, 2].max():.1f} m")

    crs = sparse.block.crs
    grid = grid_from_points(pts, args.dsm_gsd, crs)
    if args.clip_to_commercial and COMMERCIAL_DSM.exists():
        import rasterio

        with rasterio.open(COMMERCIAL_DSM) as src:
            b = src.bounds
        grid = Grid.from_bounds((b.left, b.bottom, b.right, b.top), args.dsm_gsd, crs)
    print(f"\nDSM 格网 {grid.width}x{grid.height} @ {grid.gsd:.9f} m，范围 {grid.bounds}")

    print("\n=== 密集匹配 ===")
    t = time.time()
    cfg = DenseConfig(
        n_layers=args.layers,
        z_margin_m=args.z_margin,
        max_views=args.max_views,
        tile=args.tile,
        pyramid_levels=args.pyramid,
    )
    if args.no_antialias:
        import ms_mosaic.dense as dense_mod

        dense_mod.antialias_sigma = lambda *_: 0.0
    field = compute_height_field(
        grid,
        pts,
        {i: sparse.camera for i in sparse.poses},
        sparse.poses,
        sparse.image_paths,
        cfg=cfg,
        log=print,
    )
    dense_s = time.time() - t
    print(f"密集匹配 {dense_s:.1f}s，解出 {field.stats['fill_ratio']:.1%}，"
          f"平均视图数 {field.stats['mean_views']:.1f}")
    print(f"高程 {field.stats['z_min']:.1f} / {field.stats['z_median']:.1f} / "
          f"{field.stats['z_max']:.1f} m")

    # 自洽性：密集高程面必须先跟自己的稀疏连接点吻合，才谈得上跟商业成品吻合。
    # 这个指标不依赖外部数据，是调密集匹配时最快的反馈。
    inv = ~grid.transform
    cc, rr = inv * (pts[:, 0], pts[:, 1])
    cc = np.floor(np.asarray(cc)).astype(int)
    rr = np.floor(np.asarray(rr)).astype(int)
    ok = (cc >= 0) & (cc < grid.width) & (rr >= 0) & (rr < grid.height)
    got = field.z[rr[ok], cc[ok]]
    both = np.isfinite(got)
    if both.sum() > 50:
        d = got[both] - pts[ok, 2][both]
        bias = float(np.mean(d))
        print(
            f"密集面 vs 稀疏点：n={both.sum()} 偏差={bias:+.3f} m "
            f"去偏中误差={np.sqrt(np.mean((d - bias) ** 2)):.3f} m "
            f"P50|d|={np.percentile(np.abs(d - bias), 50):.3f} "
            f"P90|d|={np.percentile(np.abs(d - bias), 90):.3f} m"
        )

    print("\n=== DSM ===")
    dsm = build_dsm(field)
    for k in ("dropped_low_confidence", "dropped_spikes", "fill_ratio", "z_min", "z_median", "z_max"):
        print(f"  {k:<26}{dsm.stats[k]}")
    out_path = write_geotiff(dsm, args.out / "DSM.tif")
    print(f"写出 {out_path}")

    if COMMERCIAL_DSM.exists():
        print("\n=== 与商业 DSM 比对 ===")
        print(compare_dsm(out_path, COMMERCIAL_DSM).to_text())
    else:
        print(f"\n未找到商业 DSM：{COMMERCIAL_DSM}")

    print(f"\n总耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
