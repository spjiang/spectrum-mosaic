"""每条航带单独和厂家 MergeResult 配准，找出各航带相对厂家成果的平移。

用法：python scripts/register_vendor.py <架次目录> <MergeResult.hdr> [--sign 1] [--along 0]
输出每条航带的 (东, 北) 平移、换算到该航带的 (沿航向, 旁向)，以及对齐后的相关系数。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compare_vendor import vendor_on_grid  # noqa: E402
from pb_mosaic.geom.footprint import strip_geometry  # noqa: E402
from pb_mosaic.geom.grid import grid_from_bounds  # noqa: E402
from pb_mosaic.geom.sensor import sensor_from  # noqa: E402
from pb_mosaic.geom.strips import split_strips  # noqa: E402
from pb_mosaic.geom.trajectory import build_trajectory  # noqa: E402
from pb_mosaic.ingest.envi import envi_list, read_header  # noqa: E402
from pb_mosaic.ingest.flight import check_flight  # noqa: E402
from pb_mosaic.mosaic.align import ncc_search, render_strip  # noqa: E402


def vendor_bounds(hdr_path: Path, epsg: int) -> tuple[float, float, float, float]:
    hdr = read_header(hdr_path)
    g = [float(x) for x in envi_list(hdr.fields["geo points"])]
    lat = g[2::4]
    lon = g[3::4]
    tf = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    x, y = tf.transform(lon, lat)
    return min(x), min(y), max(x), max(y)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path)
    ap.add_argument("vendor_hdr", type=Path)
    ap.add_argument("--band", type=int, default=150)
    ap.add_argument("--gsd", type=float, default=0.1)
    ap.add_argument("--sign", type=int, default=1)
    ap.add_argument("--along", type=float, default=0.0)
    ap.add_argument("--boresight", type=float, default=0.0)
    ap.add_argument("--max-shift-m", type=float, default=60.0)
    args = ap.parse_args()
    params = {"sample_sign": args.sign, "along_offset_m": args.along, "boresight_px": args.boresight}
    flight = check_flight(args.flight)
    traj = build_trajectory(flight.gps)
    strips = split_strips(traj)
    sensor = sensor_from(flight.hdr, params)
    geoms = [strip_geometry(traj, s, sensor) for s in strips]
    b = np.array([g.bounds() for g in geoms] + [vendor_bounds(args.vendor_hdr, traj.epsg)])
    pad = args.max_shift_m
    grid = grid_from_bounds(
        traj.epsg, b[:, 0].min() - pad, b[:, 1].min() - pad, b[:, 2].max() + pad, b[:, 3].max() + pad, args.gsd
    )
    theirs, ok_v = vendor_on_grid(args.vendor_hdr, args.band, grid)
    plane = np.asarray(flight.open_cube()[:, args.band - 1, :]).astype(np.float32)
    max_px = int(args.max_shift_m / args.gsd)
    out = []
    for g in geoms:
        mine, ok = render_strip(g, grid, plane)
        peaks, _ = ncc_search(theirs, ok_v, mine, ok, max_px, min_cover=0.5)
        dy, dx, ncc = (peaks[0].drow, peaks[0].dcol, peaks[0].ncc) if peaks else (0, 0, float("nan"))
        h = np.radians(g.strip.heading_deg)
        east, north = dx * args.gsd, -dy * args.gsd
        out.append(
            {
                "strip": g.strip.index,
                "heading": round(g.strip.heading_deg, 1),
                "shift_east_north_m": [round(east, 2), round(north, 2)],
                "shift_along_cross_m": [
                    round(float(east * np.sin(h) + north * np.cos(h)), 2),
                    round(float(east * np.cos(h) - north * np.sin(h)), 2),
                ],
                "ncc": round(ncc, 4),
            }
        )
    print(json.dumps({"params": params, "strips": out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
