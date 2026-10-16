"""把一次产出的「拼图结果」和商业成品做明文对比。

    python scripts/compare_delivery.py \\
        --ours /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/server/worker/runs/replay_max_20251017_rgb/拼图结果 \\
        --theirs /Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合/prod/docs/需求/测试正式数据/MAX_20251017/拼图结果
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ms_mosaic.compare import write_delivery_report
from ms_mosaic.local_defaults import BENCHMARK

DEFAULT_COMM = BENCHMARK


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", type=Path, required=True)
    ap.add_argument("--theirs", type=Path, default=DEFAULT_COMM)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or (args.ours.parent / "比对报告.md")
    rep = write_delivery_report(args.ours, args.theirs, out)
    print(out.read_text(encoding="utf-8"))
    raise SystemExit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
