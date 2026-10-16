"""Phase 1 真实数据体检：特征 → 像对 → 匹配 → 连接点。

只读输入目录，不写任何成果，只在 --cache 目录下缓存特征。
用法：
    python scripts/phase1_check.py --max-index 40
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from ms_mosaic.catalog import scan_directory
from ms_mosaic.features import extract_file
from ms_mosaic.matching import match_pair
from ms_mosaic.pairs import select_pairs
from ms_mosaic.scene import PRIMARY_BAND, build_block, usable_shots
from ms_mosaic.local_defaults import CACHE, INPUT
from ms_mosaic.tracks import build_tracks

DEFAULT_INPUT = INPUT


def _extract_one(args):
    index, path, cache_dir, max_features = args
    feats = extract_file(Path(path), cache_dir=Path(cache_dir), max_features=max_features)
    return index, len(feats)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--max-index", type=int, default=40)
    ap.add_argument("--max-features", type=int, default=8192)
    ap.add_argument("--max-neighbors", type=int, default=12)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    t0 = time.time()
    shots = scan_directory(args.input, max_index=args.max_index)
    keep, reasons = usable_shots(shots)
    block = build_block(keep)
    primary = block.primary()
    print(f"扫描曝光      {len(shots)}，可用 {len(keep)}，过滤 {reasons}")
    print(f"坐标系        {block.crs}")
    print(f"概略地面高程  {block.ground_z:.2f} m")
    print(f"主波段 {PRIMARY_BAND} 影像 {len(primary)} 张，耗时 {time.time() - t0:.1f}s")

    cam = block.cameras[PRIMARY_BAND]
    print(f"相机初值      {cam.width}x{cam.height}  f={cam.f:.1f}px  主点=({cam.cx:.1f},{cam.cy:.1f})")

    t1 = time.time()
    cameras = {im.index: cam for im in primary}
    poses = {im.index: block.poses[im.index] for im in primary}
    candidates = select_pairs(
        cameras, poses, block.ground_z, max_neighbors=args.max_neighbors
    )
    print(f"\n像对筛选      {len(candidates)} 对 / 全组合 {len(primary) * (len(primary) - 1) // 2} 对"
          f"，耗时 {time.time() - t1:.1f}s")
    if candidates:
        ov = np.array([c.overlap for c in candidates])
        print(f"  重叠率      min={ov.min():.2f} 中位={np.median(ov):.2f} max={ov.max():.2f}")

    t2 = time.time()
    args.cache.mkdir(parents=True, exist_ok=True)
    jobs = [(im.index, str(im.path), str(args.cache), args.max_features) for im in primary]
    counts: dict[int, int] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index, n in pool.map(_extract_one, jobs):
            counts[index] = n
    nfeat = np.array(sorted(counts.values()))
    print(f"\n特征提取      {len(primary)} 张，耗时 {time.time() - t2:.1f}s "
          f"（{(time.time() - t2) / max(1, len(primary)):.2f}s/张）")
    print(f"  每张特征数  min={nfeat.min()} 中位={int(np.median(nfeat))} max={nfeat.max()} "
          f"总计={nfeat.sum()}")

    feats = {im.index: extract_file(im.path, cache_dir=args.cache) for im in primary}

    t3 = time.time()
    matches = []
    inlier_counts = []
    models = {"essential": 0, "homography": 0}
    for c in candidates:
        pm = match_pair(c.i, c.j, feats[c.i], feats[c.j], cam, cam)
        if pm is None:
            continue
        matches.append(pm)
        inlier_counts.append(len(pm))
        models[pm.model] = models.get(pm.model, 0) + 1
    print(f"\n匹配          {len(matches)}/{len(candidates)} 对成功，耗时 {time.time() - t3:.1f}s "
          f"（{(time.time() - t3) / max(1, len(candidates)):.2f}s/对）")
    if inlier_counts:
        ic = np.array(inlier_counts)
        print(f"  每对内点    min={ic.min()} 中位={int(np.median(ic))} max={ic.max()} 总计={ic.sum()}")
        print(f"  模型分布    {models}")

    t4 = time.time()
    tracks = build_tracks(matches)
    print(f"\n连接点        {len(tracks)} 条轨迹，{tracks.total_observations()} 个观测，"
          f"耗时 {time.time() - t4:.1f}s")
    if len(tracks):
        lens = tracks.lengths
        per_image = tracks.per_image_counts()
        pv = np.array(sorted(per_image.values()))
        print(f"  轨迹长度    min={lens.min()} 中位={int(np.median(lens))} max={lens.max()} "
              f"均值={lens.mean():.2f}")
        print(f"  每张连接点  min={pv.min()} 中位={int(np.median(pv))} max={pv.max()}")
        print(f"  覆盖影像    {len(per_image)}/{len(primary)}")
    print(f"\n总耗时        {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
