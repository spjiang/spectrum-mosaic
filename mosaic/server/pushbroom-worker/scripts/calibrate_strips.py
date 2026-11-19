"""相邻航带互相比：各自单独成图，在重叠区量平移。几何参数对了，平移应接近 0。

往返航带方向相反，所以：
- 时间错位（沿航向偏移）在相邻两带上方向相反，沿航向平移 ≈ 2 × along_offset
- 狭缝中心偏差在相邻两带上方向相反，旁向平移 ≈ 2 × boresight × gsd
- 像元方向反了，每条航带都以自己的中线为轴镜像，相邻往返航带出现约两倍航带间距的旁向平移
- 往返航带对（N→S 与 S→N）的零点不一致，说明有随时间漂移的 GPS 误差，靠 --align 补偿

不给的参数用 pb_mosaic.geom.sensor 里的默认标定值。
用法：python scripts/calibrate_strips.py <架次目录> [--sign -1] [--along 3.5] [--boresight -49] [--align]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pb_mosaic.geom.footprint import strip_geometry  # noqa: E402
from pb_mosaic.geom.grid import grid_from_bounds  # noqa: E402
from pb_mosaic.geom.sensor import sensor_from  # noqa: E402
from pb_mosaic.geom.strips import split_strips  # noqa: E402
from pb_mosaic.geom.trajectory import build_trajectory  # noqa: E402
from pb_mosaic.ingest.flight import check_flight  # noqa: E402
from pb_mosaic.mosaic.align import AlignParams, align_strips, ncc_search, render_strip  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path)
    ap.add_argument("--band", type=int, default=150)
    ap.add_argument("--gsd", type=float, default=0.05)
    ap.add_argument("--sign", type=int)
    ap.add_argument("--along", type=float)
    ap.add_argument("--boresight", type=float)
    ap.add_argument("--gsd-cross", type=float)
    ap.add_argument("--max-shift-m", type=float, default=15.0)
    ap.add_argument("--align", action="store_true", help="先做航带间配准，再量残差")
    args = ap.parse_args()
    given = {
        "sample_sign": args.sign,
        "along_offset_m": args.along,
        "boresight_px": args.boresight,
        "gsd_cross_m": args.gsd_cross,
    }
    params = {k: v for k, v in given.items() if v is not None}
    flight = check_flight(args.flight)
    traj = build_trajectory(flight.gps)
    strips = split_strips(traj)
    sensor = sensor_from(flight.hdr, params)
    geoms = [strip_geometry(traj, s, sensor) for s in strips]
    bounds = np.array([g.bounds() for g in geoms])
    grid = grid_from_bounds(
        traj.epsg, bounds[:, 0].min(), bounds[:, 1].min(), bounds[:, 2].max(), bounds[:, 3].max(), args.gsd
    )
    plane = np.asarray(flight.open_cube()[:, args.band - 1, :]).astype(np.float32)
    alignment = None
    if args.align:
        alignment = align_strips(geoms, lambda a, b: plane[a:b], traj.epsg, AlignParams())
        geoms = [g.translated(*alignment.offsets[g.strip.index]) for g in geoms]
    imgs = [render_strip(g, grid, plane) for g in geoms]
    max_px = int(args.max_shift_m / args.gsd)
    pairs = []
    for k in range(len(geoms) - 1):
        a, ma = imgs[k]
        b, mb = imgs[k + 1]
        if (ma & mb).sum() < 5000:
            continue
        peaks, n_overlap = ncc_search(a, ma, b, ma & mb, max_px)
        if not peaks:
            continue
        dy, dx, ncc = peaks[0].drow, peaks[0].dcol, peaks[0].ncc
        # 换到航带 k 的沿航向 / 旁向
        h = np.radians(geoms[k].strip.heading_deg)
        east, north = dx * args.gsd, -dy * args.gsd
        along = east * np.sin(h) + north * np.cos(h)
        cross = east * np.cos(h) - north * np.sin(h)
        pairs.append(
            {
                "pair": [k, k + 1],
                "overlap_m2": round(n_overlap * args.gsd**2, 1),
                "shift_east_north_m": [round(east, 3), round(north, 3)],
                "shift_along_cross_m": [round(float(along), 3), round(float(cross), 3)],
                "ncc": round(ncc, 4),
            }
        )
    report = {"params": params, "sensor": sensor.as_dict(), "pairs": pairs}
    if alignment:
        report["alignment"] = alignment.as_dict()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
