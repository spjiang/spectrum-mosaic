"""切航带：航迹角稳定、速度正常的连续一段算一条航带，转弯和悬停不参与拼图。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from pb_mosaic.geom.trajectory import Trajectory
from pb_mosaic.ingest.flight import MAX_GPS_GAP_S


@dataclass
class StripParams:
    # 与航带起始方向相差超过这个角度就算转弯
    heading_tol_deg: float = 15.0
    # 低于中位地速的这个比例算悬停或起降
    min_speed_ratio: float = 0.5
    # 太短的一段不当航带
    min_length_m: float = 30.0
    # 两端航向偏离本航带中位航向超过这个角度、或地速低于中位地速这个比例的记录裁掉：
    # 刚开始转弯、刹车时几何不稳，接缝处会错开
    steady_heading_deg: float = 2.0
    steady_speed_ratio: float = 0.9
    # 再各裁掉几秒
    trim_s: float = 1.0


@dataclass
class Strip:
    index: int
    line_start: int  # 含
    line_end: int  # 不含
    heading_deg: float
    length_m: float
    speed_mps: float
    duration_s: float

    @property
    def lines(self) -> int:
        return self.line_end - self.line_start

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _angdiff(a: np.ndarray | float, b: float) -> np.ndarray | float:
    return np.abs((np.asarray(a) - b + 180.0) % 360.0 - 180.0)


def _circular_mean(deg: np.ndarray) -> float:
    r = np.radians(deg)
    return float(np.degrees(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360.0)


def _segments(traj: Trajectory, p: StripParams) -> list[tuple[int, int]]:
    """返回定位记录下标区间 [a, b]（含）。"""
    heading = traj.rec_heading_deg
    speed = traj.rec_speed
    moving = speed >= p.min_speed_ratio * float(np.median(speed))
    gap_after = np.append(np.diff(traj.rec_t) > MAX_GPS_GAP_S, False)
    out: list[tuple[int, int]] = []
    n = heading.size
    k = 0
    while k < n:
        if not moving[k]:
            k += 1
            continue
        a = k
        ref = float(heading[k])
        members = [heading[k]]
        k += 1
        while k < n and moving[k] and not gap_after[k - 1] and _angdiff(heading[k], ref) <= p.heading_tol_deg:
            members.append(heading[k])
            ref = _circular_mean(np.asarray(members))
            k += 1
        out.append((a, k - 1))
    return out


def _steady(traj: Trajectory, a: int, b: int, p: StripParams) -> tuple[int, int]:
    """把 [a, b] 两端不平稳的记录去掉。"""
    heading = traj.rec_heading_deg[a : b + 1]
    speed = traj.rec_speed[a : b + 1]
    ref_h = _circular_mean(heading)
    ref_v = float(np.median(speed))
    ok = (_angdiff(heading, ref_h) <= p.steady_heading_deg) & (speed >= p.steady_speed_ratio * ref_v)
    idx = np.nonzero(ok)[0]
    if idx.size == 0:
        return a, a - 1
    return a + int(idx[0]), a + int(idx[-1])


def split_strips(traj: Trajectory, params: StripParams | None = None) -> list[Strip]:
    p = params or StripParams()
    strips: list[Strip] = []
    for a, b in _segments(traj, p):
        a, b = _steady(traj, a, b, p)
        if b <= a:
            continue
        t0 = traj.rec_t[a] + p.trim_s
        t1 = traj.rec_t[b] - p.trim_s
        if t1 <= t0:
            continue
        first, last = int(traj.clock.rec_lines[0]), int(traj.clock.rec_lines[-1])
        l0 = max(first, int(np.ceil(float(traj.clock.lines_at(np.array([t0]))[0]))))
        l1 = min(last + 1, int(np.floor(float(traj.clock.lines_at(np.array([t1]))[0]))) + 1)
        if l1 - l0 < 2:
            continue
        x0, y0 = traj.position(np.array([l0], dtype=np.float64))
        x1, y1 = traj.position(np.array([l1 - 1], dtype=np.float64))
        length = float(np.hypot(x1[0] - x0[0], y1[0] - y0[0]))
        if length < p.min_length_m:
            continue
        strips.append(
            Strip(
                index=len(strips),
                line_start=l0,
                line_end=l1,
                heading_deg=_circular_mean(traj.rec_heading_deg[a : b + 1]),
                length_m=length,
                speed_mps=float(np.median(traj.rec_speed[a : b + 1])),
                duration_s=float(t1 - t0),
            )
        )
    return strips
