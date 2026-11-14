"""Phase 2：在真实数据上跑主波段空三，并与 LiMapper 报告的指标对照。

只读输入目录；特征缓存写到 --cache。
    python scripts/phase2_at.py --max-index 60
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from ms_mosaic.at import run_at
from ms_mosaic.catalog import scan_directory
from ms_mosaic.features import extract_file
from ms_mosaic.matching import match_pair
from ms_mosaic.pairs import select_pairs
from ms_mosaic.scene import PRIMARY_BAND, build_block, usable_shots
from ms_mosaic.local_defaults import CACHE, INPUT
from ms_mosaic.tracks import build_tracks

DEFAULT_INPUT = INPUT
_SHARED: dict = {}


def _extract(job):
    index, path, cache_dir, max_features = job
    extract_file(Path(path), cache_dir=Path(cache_dir), max_features=max_features)
    return index


def _init_match(cache_dir, indices, paths, cam):
    _SHARED["cam"] = cam
    _SHARED["feats"] = {
        i: extract_file(Path(p), cache_dir=Path(cache_dir)) for i, p in zip(indices, paths)
    }


def _match(job):
    i, j = job
    cam = _SHARED["cam"]
    pm = match_pair(i, j, _SHARED["feats"][i], _SHARED["feats"][j], cam, cam)
    if pm is None:
        return None
    return (pm.i, pm.j, pm.indices, pm.model)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--max-index", type=int, default=60)
    ap.add_argument("--max-features", type=int, default=8192)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    from ms_mosaic.matching import PairMatches

    t0 = time.time()
    shots = scan_directory(args.input, max_index=args.max_index)
    keep, reasons = usable_shots(shots)
    block = build_block(keep)
    primary = block.primary()
    cam = block.cameras[PRIMARY_BAND]
    print(f"曝光 {len(shots)} → 可用 {len(keep)}（过滤 {reasons}）")
    print(f"主波段 {PRIMARY_BAND}：{len(primary)} 张 {cam.width}x{cam.height}，f 初值 {cam.f:.1f}px")

    cameras = {im.index: cam for im in primary}
    poses = {im.index: block.poses[im.index] for im in primary}
    candidates = select_pairs(cameras, poses, block.ground_z)
    print(f"像对 {len(candidates)} 对（全组合 {len(primary) * (len(primary) - 1) // 2}）")

    args.cache.mkdir(parents=True, exist_ok=True)
    t = time.time()
    jobs = [(im.index, str(im.path), str(args.cache), args.max_features) for im in primary]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(_extract, jobs))
    print(f"特征提取 {time.time() - t:.1f}s")

    t = time.time()
    indices = [im.index for im in primary]
    paths = [str(im.path) for im in primary]
    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_match,
        initargs=(str(args.cache), indices, paths, cam),
    ) as pool:
        raw = list(pool.map(_match, [(c.i, c.j) for c in candidates], chunksize=4))
    matches = [PairMatches(i, j, idx, model) for r in raw if r for (i, j, idx, model) in [r]]
    print(f"匹配 {len(matches)}/{len(candidates)} 对成功，{time.time() - t:.1f}s")

    tracks = build_tracks(matches)
    print(f"连接点 {len(tracks)} 条，观测 {tracks.total_observations()}，"
          f"平均轨迹长 {tracks.lengths.mean():.2f}")

    feats = {im.index: extract_file(im.path, cache_dir=args.cache) for im in primary}
    image_camera = {im.index: PRIMARY_BAND for im in primary}
    gps = {im.index: block.gps[im.index] for im in primary}
    att = {im.index: block.poses[im.index].rotation for im in primary}

    print("\n=== 空中三角测量 ===")
    t = time.time()
    result = run_at(
        {PRIMARY_BAND: cam}, poses, tracks, feats, image_camera, gps, att, log=print
    )
    print(f"空三耗时 {time.time() - t:.1f}s")

    c = result.cameras[PRIMARY_BAND]
    print(f"\n{'参数':<5}{'解算值':>16}")
    for n in ["f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2"]:
        print(f"{n:<5}{getattr(c, n):>16.6f}")

    s = result.stats
    print(f"\n{'指标':<26}{'本程序':>16}{'LiMapper 报告':>18}")
    rows = [
        ("已注册影像", f"{s['n_images']}/{len(primary)}", "1888/1896"),
        ("稀疏 3D 点", s["n_points"], 735163),
        ("2D 观测值", s["n_observations"], 1696950),
        ("单张平均观测", f"{s['mean_obs_per_image']:.0f}", 898),
        ("平均轨迹长度", f"{s['mean_track_length']:.2f}", "2.31"),
        ("均方根重投影误差", f"{s['rms_reprojection_px']:.4f} px", "0.410198 px"),
        ("重投影误差均值", f"{s['mean_reprojection_px']:.4f} px", "0.445461 px"),
        ("GPS 配准 RMSE", f"{s['gps_rmse_m']:.3f} m", "6.2806 m"),
        ("  RMSE-X", f"{s['gps_rmse_x_m']:.3f} m", "0.68763 m"),
        ("  RMSE-Y", f"{s['gps_rmse_y_m']:.3f} m", "0.912037 m"),
        ("  RMSE-Z", f"{s['gps_rmse_z_m']:.3f} m", "6.17587 m"),
    ]
    for name, ours, theirs in rows:
        print(f"{name:<26}{str(ours):>16}{str(theirs):>18}")

    pts = result.points
    print(f"\n稀疏点高程 min={pts[:, 2].min():.1f} 中位={np.median(pts[:, 2]):.1f} "
          f"max={pts[:, 2].max():.1f} m")
    print("商业 DSM 对照   min=1635.5 中位=1721.3 max=1830.2 m")
    print(f"\n总耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
