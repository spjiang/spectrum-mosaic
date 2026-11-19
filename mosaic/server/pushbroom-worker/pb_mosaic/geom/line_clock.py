"""行号 → 定位时间。

figspec.gps 的记录器大约每 1.04 秒记一条：取当时的行计数（每 7 行更新一次）和
「最近一次」GPS 定位。定位本身每整秒一个、时间准确，但到记录时已经过去 0～1 秒，
这个滞后随记录器周期慢慢累积，超过 1 秒就跳过一个定位（表现为 dt≈2 s）。
按记录逐条把行号和位置配对，位置会有 0～1 秒（约 0～2.6 m）的锯齿误差。

相机行频是恒定的，所以用全部记录拟合「行号 = 行频 × 时间」，取残差下包络
（最新鲜的定位）作为零滞后，行的时间就不受记录器抖动影响。剩下的固定滞后
和安装偏差一起由沿航向偏移标定。残差不像上述锯齿时（相机真丢了帧、中途停过），
退回逐条记录插值，并给出提示。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pb_mosaic.ingest.gps import GpsTable

# 记录器取到的定位最多滞后多少秒仍算正常；超过说明行号与时间不是线性关系
MAX_AGE_SPAN_S = 1.5
# 下包络取残差的这个分位数，避开个别异常记录
ENVELOPE_QUANTILE = 0.02
# 至少这么多条定位才拟合，太少时锯齿统计不出来
MIN_RECORDS = 10


@dataclass(frozen=True)
class LineClock:
    method: str  # "linear"：全局线性；"records"：逐条记录插值
    rate_hz: float
    rec_lines: np.ndarray
    rec_t: np.ndarray
    t_ref: float = 0.0
    lag_s: float = 0.0
    age_span_s: float = 0.0
    note: str = ""

    def time(self, lines: np.ndarray) -> np.ndarray:
        lines = np.asarray(lines, dtype=np.float64)
        if self.method == "linear":
            return self.t_ref + lines / self.rate_hz - self.lag_s
        return np.interp(lines, self.rec_lines, self.rec_t)

    def lines_at(self, t: np.ndarray) -> np.ndarray:
        t = np.asarray(t, dtype=np.float64)
        if self.method == "linear":
            return (t - self.t_ref + self.lag_s) * self.rate_hz
        return np.interp(t, self.rec_t, self.rec_lines)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "rate_hz": round(self.rate_hz, 4),
            "age_span_s": round(self.age_span_s, 3),
            "note": self.note,
        }


def fit_line_clock(gps: GpsTable) -> LineClock:
    lines = gps.lines.astype(np.float64)
    t = gps.time_s.astype(np.float64)
    fallback_rate = float((lines[-1] - lines[0]) / max(t[-1] - t[0], 1e-9))

    def records(note: str) -> LineClock:
        return LineClock("records", fallback_rate, lines, t, note=note)

    if lines.size < MIN_RECORDS:
        return records(f"定位只有 {lines.size} 条，按逐条记录插值")
    t_ref = float(t[0])
    rate = float(np.polyfit(t - t_ref, lines, 1)[0])
    if not np.isfinite(rate) or rate <= 0:
        return records("行号与时间拟合失败，按逐条记录插值")
    # age = 行号换算的时间 − 定位时间：记录器拿到定位时，这个定位已经过去多久
    age = lines / rate - (t - t_ref)
    lag = float(np.quantile(age, ENVELOPE_QUANTILE))
    # 开机头一两条常有更大的滞后，按分位数看整体锯齿幅度
    span = float(np.quantile(age, 1.0 - ENVELOPE_QUANTILE)) - lag
    if span > MAX_AGE_SPAN_S:
        return records(
            f"行号与定位时间偏离线性 {span:.2f} 秒（可能丢帧或中途暂停），按逐条记录插值，几何精度会下降"
        )
    return LineClock(
        "linear",
        rate,
        lines,
        t,
        t_ref=t_ref,
        lag_s=lag,
        age_span_s=span,
        note=f"行频 {rate:.3f} Hz，记录器滞后 0～{span:.2f} 秒已按时间修正",
    )
