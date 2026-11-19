"""和厂家 MergeResult 比几何：同一格网上各出一张单波段图，看相关系数和整体偏移。

用法：
  python scripts/compare_vendor.py <架次目录> <MergeResult.hdr> [--band 150] [--gsd 0.1]
        [--sign 1] [--along 0] [--save out.npz]

厂家头里的 cp gps matrix 是 [lon, lat] = M · [line, sample, 1]。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pb_mosaic.geom.footprint import strip_geometry  # noqa: E402
from pb_mosaic.geom.grid import grid_from_bounds  # noqa: E402
from pb_mosaic.geom.sensor import sensor_from  # noqa: E402
from pb_mosaic.geom.strips import split_strips  # noqa: E402
from pb_mosaic.geom.trajectory import build_trajectory  # noqa: E402
from pb_mosaic.ingest.envi import envi_list, open_bil, read_header  # noqa: E402
from pb_mosaic.ingest.flight import check_flight  # noqa: E402
from pb_mosaic.mosaic.merge import strip_boxes, select_block  # noqa: E402


def ours(flight_dir: Path, band: int, gsd: float, params: dict):
    flight = check_flight(flight_dir)
    traj = build_trajectory(flight.gps)
    strips = split_strips(traj)
    sensor = sensor_from(flight.hdr, params)
    geoms = [strip_geometry(traj, s, sensor) for s in strips]
    bounds = np.array([g.bounds() for g in geoms])
    grid = grid_from_bounds(traj.epsg, bounds[:, 0].min(), bounds[:, 1].min(), bounds[:, 2].max(), bounds[:, 3].max(), gsd)
    line, samp, sid = select_block(strip_boxes(geoms, grid), grid, 0, grid.height)
    cube = flight.open_cube()
    img = np.zeros((grid.height, grid.width), dtype=np.float32)
    ok = line >= 0
    band_plane = np.asarray(cube[:, band - 1, :])
    img[ok] = band_plane[line[ok], samp[ok]]
    return grid, img, ok, strips, sid


def vendor_on_grid(hdr_path: Path, band: int, grid) -> tuple[np.ndarray, np.ndarray]:
    hdr = read_header(hdr_path)
    m = [float(x) for x in envi_list(hdr.fields["cp gps matrix"])]
    a = np.array([[m[0], m[1]], [m[3], m[4]]])
    t = np.array([m[2], m[5]])
    spe = hdr_path.with_suffix(".spe")
    cube = open_bil(spe, hdr)
    plane = np.asarray(cube[:, band - 1, :]).astype(np.float32)
    cols = np.arange(grid.width)
    rows = np.arange(grid.height)
    xx, yy = np.meshgrid(grid.col_x(cols), grid.row_y(rows))
    tf = Transformer.from_crs(CRS.from_epsg(grid.epsg), CRS.from_epsg(4326), always_xy=True)
    lon, lat = tf.transform(xx.ravel(), yy.ravel())
    ls = np.linalg.solve(a, np.vstack([lon - t[0], lat - t[1]]))
    li = np.rint(ls[0]).astype(np.int64)
    si = np.rint(ls[1]).astype(np.int64)
    ok = (li >= 0) & (li < hdr.lines) & (si >= 0) & (si < hdr.samples)
    out = np.zeros(xx.size, dtype=np.float32)
    out[ok] = plane[li[ok], si[ok]]
    ok &= out > 0
    return out.reshape(xx.shape), ok.reshape(xx.shape)


def phase_shift(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
    """b 相对 a 的平移（行, 列）和峰值强度。"""
    def prep(x):
        x = np.where(mask, x, 0.0)
        mu = x[mask].mean()
        x = np.where(mask, x - mu, 0.0)
        return x
    fa, fb = np.fft.fft2(prep(a)), np.fft.fft2(prep(b))
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    c = np.real(np.fft.ifft2(r))
    k = np.unravel_index(np.argmax(c), c.shape)
    dy = k[0] if k[0] <= c.shape[0] // 2 else k[0] - c.shape[0]
    dx = k[1] if k[1] <= c.shape[1] // 2 else k[1] - c.shape[1]
    return float(dy), float(dx), float(c.max())


def corr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    x, y = a[mask], b[mask]
    return float(np.corrcoef(x, y)[0, 1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("flight", type=Path)
    ap.add_argument("vendor_hdr", type=Path)
    ap.add_argument("--band", type=int, default=150)
    ap.add_argument("--gsd", type=float, default=0.1)
    ap.add_argument("--sign", type=int, default=1)
    ap.add_argument("--along", type=float, default=0.0)
    ap.add_argument("--save", type=Path)
    args = ap.parse_args()
    params = {"sample_sign": args.sign, "along_offset_m": args.along}
    grid, mine, ok1, strips, sid = ours(args.flight, args.band, args.gsd, params)
    theirs, ok2 = vendor_on_grid(args.vendor_hdr, args.band, grid)
    both = ok1 & ok2
    dy, dx, peak = phase_shift(mine, theirs, both)
    per_strip = {}
    for s in strips:
        m = both & (sid == s.index)
        if m.sum() > 1000:
            per_strip[s.index] = round(corr(mine, theirs, m), 4)
    res = {
        "params": params,
        "grid": grid.as_dict(),
        "strips": len(strips),
        "overlap_px": int(both.sum()),
        "ours_px": int(ok1.sum()),
        "vendor_px": int(ok2.sum()),
        "corr": round(corr(mine, theirs, both), 4),
        "corr_per_strip": per_strip,
        "shift_rows_cols": [dy, dx],
        "shift_m_north_east": [round(-dy * args.gsd, 3), round(dx * args.gsd, 3)],
        "peak": round(peak, 4),
    }
    print(json.dumps(res, ensure_ascii=False))
    if args.save:
        np.savez_compressed(args.save, mine=mine, theirs=theirs, ok1=ok1, ok2=ok2, sid=sid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
