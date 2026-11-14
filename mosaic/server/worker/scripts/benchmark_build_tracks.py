#!/usr/bin/env python3
"""本机连接点墙钟：合成边或从 npz 读入 PairMatches 快照。

npz 由后续工具导出，字段示例：
  i, j, indices (object array of (N,2) int32), model (str)

用法:
  python scripts/benchmark_build_tracks.py
  python scripts/benchmark_build_tracks.py --npz /path/to/matches.npz
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ms_mosaic.matching import PairMatches
from ms_mosaic.tracks import build_tracks


def _synthetic_heavy():
    matches = []
    for k in range(400):
        i, j = k * 2, k * 2 + 1
        n = 120
        idx = np.column_stack([np.arange(n, dtype=np.int32), np.arange(n, dtype=np.int32)])
        matches.append(PairMatches(i, j, idx, "essential"))
    return matches


def _load_npz(path: Path) -> list[PairMatches]:
    data = np.load(path, allow_pickle=True)
    out = []
    for i, j, idx, model in zip(data["i"], data["j"], data["indices"], data["model"], strict=False):
        out.append(PairMatches(int(i), int(j), np.asarray(idx, np.int32), str(model)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", type=Path, default=None, help="PairMatches 快照")
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args()
    matches = _load_npz(args.npz) if args.npz else _synthetic_heavy()
    edges = sum(int(m.indices.shape[0]) for m in matches)
    print(f"像对 {len(matches)}，内点边 {edges}")

    build_tracks(matches)
    best = None
    for _ in range(max(1, args.repeat)):
        t0 = time.perf_counter()
        tracks = build_tracks(matches)
        elapsed = time.perf_counter() - t0
        best = elapsed if best is None else min(best, elapsed)
        print(f"轨迹 {len(tracks)}，观测 {tracks.total_observations()}，{elapsed:.3f} s")
    if best is not None:
        print(f"最快 {best:.3f} s")


if __name__ == "__main__":
    main()
