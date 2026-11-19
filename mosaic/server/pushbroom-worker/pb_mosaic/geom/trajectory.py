"""轨迹：定位投到 UTM，行号先换成时间（见 line_clock），再按时间插值位置。

定位约 1 秒一条，一条约 70 行。方向取前后几秒的最小二乘速度，
避免单条定位的抖动把整行转偏。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import CRS, Transformer

from pb_mosaic.geom.line_clock import LineClock, fit_line_clock
from pb_mosaic.ingest.gps import GpsTable

# 速度拟合用前后各多少条定位
VELOCITY_HALF_WINDOW = 3


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) // 6.0) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def _local_velocity(t: np.ndarray, v: np.ndarray, half: int) -> np.ndarray:
    """每条记录处，用前后 half 条做直线拟合的斜率。"""
    n = t.size
    out = np.empty(n, dtype=np.float64)
    for k in range(n):
        a = max(0, k - half)
        b = min(n, k + half + 1)
        if b - a < 2:
            a, b = max(0, b - 2), min(n, a + 2)
        tt = t[a:b] - t[a:b].mean()
        denom = float(np.dot(tt, tt))
        out[k] = float(np.dot(tt, v[a:b] - v[a:b].mean()) / denom) if denom > 0 else 0.0
    return out


@dataclass
class Trajectory:
    epsg: int
    clock: LineClock
    rec_t: np.ndarray
    rec_x: np.ndarray
    rec_y: np.ndarray
    rec_z: np.ndarray
    rec_vx: np.ndarray
    rec_vy: np.ndarray

    @property
    def line_rate_hz(self) -> float:
        return self.clock.rate_hz

    @property
    def rec_speed(self) -> np.ndarray:
        return np.hypot(self.rec_vx, self.rec_vy)

    @property
    def rec_heading_deg(self) -> np.ndarray:
        """以正北为 0、顺时针为正的航迹角。"""
        return np.degrees(np.arctan2(self.rec_vx, self.rec_vy)) % 360.0

    def position(self, lines: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        t = self.clock.time(lines)
        return np.interp(t, self.rec_t, self.rec_x), np.interp(t, self.rec_t, self.rec_y)

    def direction(self, lines: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        t = self.clock.time(lines)
        vx = np.interp(t, self.rec_t, self.rec_vx)
        vy = np.interp(t, self.rec_t, self.rec_vy)
        norm = np.hypot(vx, vy)
        norm[norm == 0] = 1.0
        return vx / norm, vy / norm

    def step_per_line(self, lines: np.ndarray) -> np.ndarray:
        """沿航向每行在地面上走多少米。"""
        t = self.clock.time(lines)
        speed = np.hypot(np.interp(t, self.rec_t, self.rec_vx), np.interp(t, self.rec_t, self.rec_vy))
        return speed / self.clock.rate_hz

    def transformer_to_lonlat(self) -> Transformer:
        return Transformer.from_crs(CRS.from_epsg(self.epsg), CRS.from_epsg(4326), always_xy=True)


def build_trajectory(gps: GpsTable, *, epsg: int | None = None, clock: LineClock | None = None) -> Trajectory:
    code = epsg or utm_epsg(float(np.median(gps.lon)), float(np.median(gps.lat)))
    tf = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(code), always_xy=True)
    x, y = tf.transform(gps.lon, gps.lat)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    return Trajectory(
        epsg=code,
        clock=clock or fit_line_clock(gps),
        rec_t=gps.time_s,
        rec_x=x,
        rec_y=y,
        rec_z=gps.alt,
        rec_vx=_local_velocity(gps.time_s, x, VELOCITY_HALF_WINDOW),
        rec_vy=_local_velocity(gps.time_s, y, VELOCITY_HALF_WINDOW),
    )
