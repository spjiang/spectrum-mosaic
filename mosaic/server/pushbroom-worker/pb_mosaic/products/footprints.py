"""航带足迹 GeoJSON（WGS84 经纬度，RFC 7946），和逐条定位的轨迹表。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.geom.trajectory import Trajectory


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_footprints(
    path: Path,
    traj: Trajectory,
    geoms: list[StripGeometry],
    offsets: dict[int, tuple[float, float]],
    strip_pixels: dict[int, int] | None = None,
) -> None:
    tf = traj.transformer_to_lonlat()
    features = []
    for g in geoms:
        ring = g.outline()
        lon, lat = tf.transform(ring[:, 0], ring[:, 1])
        coords = [[round(float(x), 8), round(float(y), 8)] for x, y in zip(lon, lat)]
        dx, dy = offsets.get(g.strip.index, (0.0, 0.0))
        props = dict(g.strip.as_dict())
        props["align_offset_east_north_m"] = [round(dx, 3), round(dy, 3)]
        if strip_pixels is not None:
            props["mosaic_pixels"] = int(strip_pixels.get(g.strip.index, 0))
        features.append(
            {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [coords]}}
        )
    _write_text(path, json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False))


def write_trajectory_csv(path: Path, traj: Trajectory) -> None:
    """每条定位：时间、UTM 坐标、地速、航迹角、按行频推算的行号。供排查定位问题。"""
    lines_est = traj.clock.lines_at(traj.rec_t)
    tmp = path.with_name(path.name + ".part")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["record", "gps_time_s", "line_recorded", "line_at_fix", "x_m", "y_m", "alt_m", "speed_mps", "heading_deg"])
        for k in range(traj.rec_t.size):
            w.writerow(
                [
                    k,
                    f"{traj.rec_t[k]:.3f}",
                    int(traj.clock.rec_lines[k]),
                    f"{lines_est[k]:.1f}",
                    f"{traj.rec_x[k]:.3f}",
                    f"{traj.rec_y[k]:.3f}",
                    f"{traj.rec_z[k]:.3f}",
                    f"{float(np.hypot(traj.rec_vx[k], traj.rec_vy[k])):.3f}",
                    f"{float(traj.rec_heading_deg[k]):.2f}",
                ]
            )
    tmp.replace(path)
