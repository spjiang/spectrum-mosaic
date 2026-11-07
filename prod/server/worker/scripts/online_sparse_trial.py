#!/usr/bin/env python3
"""在 GPU 服务器上用真实测区跑一段空三，打印各阶段墙钟（不经过 RabbitMQ）。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ms_mosaic.cpu import set_job_cpus
from ms_mosaic.memory import set_job_memory_gb
from ms_mosaic.runner import run_sparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("--max-frames", type=int, default=30)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--memory-gb", type=float, default=31.0)
    ap.add_argument("--cpus", type=int, default=10)
    ap.add_argument("--cache-dir", type=Path, default=None)
    args = ap.parse_args()
    inp = args.input_dir.resolve()
    if not inp.is_dir():
        raise SystemExit(f"输入目录不存在: {inp}")
    cache = args.cache_dir or (Path("/tmp") / f"mosaic_sparse_trial_{int(time.time())}")
    cache.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        print(msg, flush=True)

    set_job_memory_gb(args.memory_gb)
    set_job_cpus(args.cpus if args.cpus > 0 else None)

    t0 = time.perf_counter()
    result = run_sparse(
        inp,
        cache,
        max_frames=args.max_frames,
        workers=args.workers,
        memory_gb=args.memory_gb,
        log=log,
    )
    wall = time.perf_counter() - t0
    out = {
        "input": str(inp),
        "max_frames": args.max_frames,
        "workers": args.workers,
        "memory_gb": args.memory_gb,
        "cpus": args.cpus,
        "cache_dir": str(cache),
        "wall_s": round(wall, 2),
        "timings": {k: round(v, 2) for k, v in result.timings.items()},
        "counts": result.counts,
    }
    print("---JSON---", flush=True)
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
