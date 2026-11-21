"""一条航带在地面上的几何：逐行投影中心、方向，正算地面点，反算行列号。

狭缝与航迹不严格垂直时（偏流、安装偏角），离中线 d 米的像元沿航向错开 shear·d 米。
shear 由航带间精配准逐条估计，见 pb_mosaic.mosaic.refine。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from pb_mosaic.geom.sensor import Sensor
from pb_mosaic.geom.strips import Strip
from pb_mosaic.geom.trajectory import Trajectory

# 反算行号的牛顿迭代次数。航带基本是直线，三四次就收敛到百分之一行
NEWTON_ITERATIONS = 4
# 边缘取样间隔（行），只用于外包框和足迹多边形
OUTLINE_STEP = 50


@dataclass
class StripGeometry:
    strip: Strip
    sensor: Sensor
    px: np.ndarray  # 每行的地面中心（已加沿航向偏移）
    py: np.ndarray
    hx: np.ndarray  # 航迹方向单位向量
    hy: np.ndarray
    step: np.ndarray  # 每行沿航向的米数
    shear: float = 0.0  # 弧度，向航迹右侧每米沿航向前移的米数
    _table: tuple | None = field(default=None, repr=False)

    @property
    def n(self) -> int:
        return int(self.px.size)

    def translated(self, dx: float, dy: float) -> "StripGeometry":
        """整条航带平移，用于航带间配准后的修正。"""
        if dx == 0 and dy == 0:
            return self
        return replace(self, px=self.px + dx, py=self.py + dy, _table=None)

    def corrected(self, dx: float, dy: float, dshear: float) -> "StripGeometry":
        """平移 (dx, dy) 并把剪切增加 dshear。"""
        moved = self.translated(dx, dy)
        return replace(moved, shear=moved.shear + dshear) if dshear else moved

    def bent(self, east: np.ndarray, north: np.ndarray) -> "StripGeometry":
        """逐行平移（东、北，米）。用来跟上沿航向变化的 GPS 误差。"""
        east = np.asarray(east, dtype=np.float64)
        north = np.asarray(north, dtype=np.float64)
        if east.shape != (self.n,) or north.shape != (self.n,):
            raise ValueError(f"逐行平移长度应为 {self.n}")
        if not np.any(east) and not np.any(north):
            return self
        return replace(self, px=self.px + east, py=self.py + north, _table=None)

    def cross_m(self, sample: np.ndarray) -> np.ndarray:
        """像元号 → 离狭缝中心的旁向米数，航迹右侧为正。"""
        return self.sensor.sign * (np.asarray(sample, dtype=np.float64) - self.sensor.center_px) * self.sensor.gsd_cross_m

    def heading_at(self, local_line: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        _, _, hx, hy = self._at(np.asarray(local_line, dtype=np.float64))
        return hx, hy

    def ground(self, local_line: np.ndarray, sample: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        i = np.asarray(local_line, dtype=np.float64)
        cx, cy, hx, hy = self._at(i)
        d = self.cross_m(sample)
        a = self.shear * d
        # 右侧方向 r = (hy, -hx)
        return cx + d * hy + a * hx, cy - d * hx + a * hy

    def _at(self, i: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        i = np.clip(i, 0.0, self.n - 1.0)
        a = np.minimum(np.floor(i).astype(np.int64), self.n - 2) if self.n > 1 else np.zeros_like(i, dtype=np.int64)
        w = i - a
        b = np.minimum(a + 1, self.n - 1)
        cx = self.px[a] * (1 - w) + self.px[b] * w
        cy = self.py[a] * (1 - w) + self.py[b] * w
        hx = self.hx[a] * (1 - w) + self.hx[b] * w
        hy = self.hy[a] * (1 - w) + self.hy[b] * w
        norm = np.hypot(hx, hy)
        norm[norm == 0] = 1.0
        return cx, cy, hx / norm, hy / norm

    def outline(self) -> np.ndarray:
        """足迹多边形，逆时针或顺时针都可，首尾闭合。(N, 2)"""
        idx = np.unique(np.append(np.arange(0, self.n, OUTLINE_STEP), self.n - 1)).astype(np.float64)
        lo = np.full_like(idx, -0.5)
        hi = np.full_like(idx, self.sensor.samples - 0.5)
        lx, ly = self.ground(idx, lo)
        rx, ry = self.ground(idx, hi)
        xs = np.concatenate([lx, rx[::-1], lx[:1]])
        ys = np.concatenate([ly, ry[::-1], ly[:1]])
        return np.column_stack([xs, ys])

    def bounds(self) -> tuple[float, float, float, float]:
        ring = self.outline()
        return float(ring[:, 0].min()), float(ring[:, 1].min()), float(ring[:, 0].max()), float(ring[:, 1].max())

    def invert(self, qx: np.ndarray, qy: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """地面点 → (航带内行号, 像元号, 是否落在航带里)。行号、像元号都是浮点。

        先按航带主方向上的投影距离查表得初值（丢帧、行频抖动时每行步长不一样，
        不能用平均步长换算），再按逐行方向做牛顿修正。
        """
        qx = np.asarray(qx, dtype=np.float64)
        qy = np.asarray(qy, dtype=np.float64)
        ox, oy, dx, dy, u = self._along_table()
        idx = np.arange(self.n, dtype=np.float64)
        uq = (qx - ox) * dx + (qy - oy) * dy
        i = np.interp(uq, u, idx, left=-1.0, right=self.n)
        du = np.gradient(u) if self.n > 1 else np.ones(1)
        du[du <= 1e-6] = 1e-6
        for _ in range(NEWTON_ITERATIONS):
            ic = np.clip(i, 0.0, self.n - 1.0)
            cx, cy, hx, hy = self._at(ic)
            err = (qx - cx) * hx + (qy - cy) * hy
            if self.shear:
                err = err - self.shear * ((qx - cx) * hy - (qy - cy) * hx)
            i = np.where((i >= 0) & (i <= self.n - 1), ic + err / np.interp(ic, idx, du), i)
        cx, cy, hx, hy = self._at(np.clip(i, 0.0, self.n - 1.0))
        cross = (qx - cx) * hy - (qy - cy) * hx
        s = self.sensor.center_px + self.sensor.sign * cross / self.sensor.gsd_cross_m
        ok = (i >= -0.5) & (i <= self.n - 0.5) & (s >= -0.5) & (s <= self.sensor.samples - 0.5)
        return i, s, ok

    def _along_table(self) -> tuple[float, float, float, float, np.ndarray]:
        """航带起点、主方向，以及每行在主方向上的累计距离（保证单调）。"""
        if self._table is None:
            ox, oy = float(self.px[0]), float(self.py[0])
            dx, dy = float(self.px[-1] - ox), float(self.py[-1] - oy)
            length = float(np.hypot(dx, dy)) or 1.0
            dx, dy = dx / length, dy / length
            u = np.maximum.accumulate((self.px - ox) * dx + (self.py - oy) * dy)
            # 悬停时投影距离不再增加，加一个极小增量让查表仍然单调
            u = u + np.arange(self.n) * 1e-9
            self._table = (ox, oy, dx, dy, u)
        return self._table


def strip_geometry(traj: Trajectory, strip: Strip, sensor: Sensor) -> StripGeometry:
    lines = np.arange(strip.line_start, strip.line_end, dtype=np.float64)
    x, y = traj.position(lines)
    hx, hy = traj.direction(lines)
    # 实际逐行步长：丢帧处一行会走两倍距离
    step = np.hypot(np.gradient(x), np.gradient(y)) if lines.size > 1 else traj.step_per_line(lines)
    return StripGeometry(
        strip=strip,
        sensor=sensor,
        px=x + sensor.along_offset_m * hx,
        py=y + sensor.along_offset_m * hy,
        hx=hx,
        hy=hy,
        step=step,
    )
