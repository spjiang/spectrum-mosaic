"""匹配失败诊断：逐对打印重叠率、原始匹配数、几何校验内点数。"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from ms_mosaic.catalog import scan_directory
from ms_mosaic.features import extract_file
from ms_mosaic.matching import cross_check, geometric_verify, knn_ratio_match
from ms_mosaic.pairs import select_pairs
from ms_mosaic.local_defaults import CACHE, INPUT
from ms_mosaic.scene import PRIMARY_BAND, build_block, usable_shots

DEFAULT_INPUT = INPUT


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--max-index", type=int, default=40)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    shots = scan_directory(args.input, max_index=args.max_index)
    keep, _ = usable_shots(shots)
    block = build_block(keep)
    primary = block.primary()
    cam = block.cameras[PRIMARY_BAND]
    cameras = {im.index: cam for im in primary}
    poses = {im.index: block.poses[im.index] for im in primary}
    candidates = select_pairs(cameras, poses, block.ground_z)
    feats = {im.index: extract_file(im.path, cache_dir=args.cache) for im in primary}
    shot_of = {im.index: im.shot_index for im in primary}
    yaw_of = {im.index: block.attitude[im.index][0] for im in primary}

    print(f"{'i':>4} {'j':>4} {'曝光':>9} {'重叠':>5} {'Δyaw':>7} {'基线m':>7} "
          f"{'单向':>6} {'交叉':>6} {'E内点':>6} {'H内点':>6}")
    rows = []
    for c in candidates[: args.limit]:
        fa, fb = feats[c.i], feats[c.j]
        one = knn_ratio_match(fa.descriptors, fb.descriptors).shape[0]
        idx = cross_check(fa.descriptors, fb.descriptors)
        n_e = n_h = 0
        if idx.shape[0] >= 8:
            pa = fa.keypoints[idx[:, 0], :2]
            pb = fb.keypoints[idx[:, 1], :2]
            import cv2

            xa, ya = cam.undistort_pixels(pa[:, 0], pa[:, 1])
            xb, yb = cam.undistort_pixels(pb[:, 0], pb[:, 1])
            na = np.stack([xa, ya], 1)
            nb = np.stack([xb, yb], 1)
            thr = 2.0 / cam.f
            _, me = cv2.findEssentialMat(na, nb, np.eye(3), method=cv2.USAC_MAGSAC,
                                         prob=0.9999, threshold=thr)
            _, mh = cv2.findHomography(na, nb, method=cv2.USAC_MAGSAC,
                                       ransacReprojThreshold=thr, confidence=0.9999)
            n_e = 0 if me is None else int(me.ravel().astype(bool).sum())
            n_h = 0 if mh is None else int(mh.ravel().astype(bool).sum())
        baseline = float(np.linalg.norm(poses[c.i].center - poses[c.j].center))
        dyaw = abs(((yaw_of[c.i] - yaw_of[c.j]) + 180) % 360 - 180)
        print(f"{c.i:>4} {c.j:>4} {shot_of[c.i]:>4}-{shot_of[c.j]:<4} {c.overlap:>5.2f} "
              f"{dyaw:>7.1f} {baseline:>7.1f} {one:>6} {idx.shape[0]:>6} {n_e:>6} {n_h:>6}")
        rows.append((c.overlap, dyaw, baseline, one, idx.shape[0], n_e, n_h))

    arr = np.array(rows)
    if arr.size:
        print("\n分段统计（按重叠率）")
        for lo, hi in [(0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.01)]:
            m = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
            if not m.any():
                continue
            print(f"  重叠 [{lo:.1f},{hi:.1f})  n={int(m.sum()):3d}  "
                  f"单向中位={np.median(arr[m, 3]):7.0f}  交叉中位={np.median(arr[m, 4]):7.0f}  "
                  f"E内点中位={np.median(arr[m, 5]):7.0f}  成功率={np.mean(arr[m, 5] >= 20):.2f}")


if __name__ == "__main__":
    main()
