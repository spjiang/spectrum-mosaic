"""命令行：不经队列直接核对或处理一个架次。

  python -m pb_mosaic check --input /data/input/pushbroom/FigSpec_260414_134505
  python -m pb_mosaic run --input ... --output /data/output/pushbroom/dev-0414 [--gsd 0.05] [--bands 1-10,150]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _parse_bands(text: str) -> list[int]:
    out: set[int] = set()
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if lo < 1 or hi < lo:
            raise argparse.ArgumentTypeError(f"波段范围不对：{part}")
        out.update(range(lo, hi + 1))
    if not out:
        raise argparse.ArgumentTypeError("至少一个波段")
    return sorted(out)


def _check(args: argparse.Namespace) -> int:
    from pb_mosaic.geom.sensor import SensorError, sensor_from
    from pb_mosaic.geom.strips import split_strips
    from pb_mosaic.geom.trajectory import build_trajectory
    from pb_mosaic.ingest.flight import FlightError, check_flight

    try:
        flight = check_flight(args.input)
    except FlightError as exc:
        print(json.dumps({"ok": False, "errors": exc.errors}, ensure_ascii=False, indent=1))
        return 1
    traj = build_trajectory(flight.gps)
    strips = split_strips(traj)
    warnings = list(flight.warnings)
    try:
        sensor: dict | None = sensor_from(flight.hdr, {}).as_dict()
    except SensorError as exc:
        sensor = None
        warnings.append(str(exc))
    out = {
        "ok": bool(strips),
        "flight": flight.summary(),
        "warnings": warnings,
        "line_clock": traj.clock.as_dict(),
        "utm_epsg": traj.epsg,
        "sensor": sensor,
        "strips": [s.as_dict() for s in strips],
    }
    if not strips:
        out["errors"] = ["没有切出可用航带"]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if strips else 1


def _run(args: argparse.Namespace) -> int:
    from pb_mosaic.mosaic.merge import Cancelled
    from pb_mosaic.pipeline import PipelineError, run

    params = json.loads(args.params) if args.params else {}
    if args.gsd is not None:
        params["gsd_m"] = args.gsd
    if args.bands:
        params["bands"] = args.bands
    if args.no_align:
        params["align"] = False
    try:
        result = run(args.input, args.output, params)
    except PipelineError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    except (Cancelled, KeyboardInterrupt):
        print("已取消", file=sys.stderr)
        return 130
    print(json.dumps({"ok": True, "products": result.products}, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pb_mosaic", description="推扫高光谱拼图")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="核对三件套并切航带，不写任何文件")
    c.add_argument("--input", type=Path, required=True)
    r = sub.add_parser("run", help="处理一个架次")
    r.add_argument("--input", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--gsd", type=float, help="成果分辨率（米），默认按相机参数")
    r.add_argument("--bands", type=_parse_bands, help="输出波段，1 起始，例如 1-10,150")
    r.add_argument("--no-align", action="store_true", help="不做航带间配准")
    r.add_argument("--params", help="任务参数 JSON，与队列消息的 params 相同")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return _check(args) if args.cmd == "check" else _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
