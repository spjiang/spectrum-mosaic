"""figspec.gps：每秒一条定位，Lines 列对应立方体行号。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

REQUIRED = ("Lines", "UnixTime", "Microseconds", "Latitude", "Longitude", "Altitude")
GPS_MAX_BYTES = 64 << 20


class GpsError(ValueError):
    pass


@dataclass
class GpsTable:
    lines: np.ndarray  # int64，立方体行号
    time_s: np.ndarray  # float64，UnixTime + 微秒
    lat: np.ndarray
    lon: np.ndarray
    alt: np.ndarray

    def __len__(self) -> int:
        return int(self.lines.size)


def parse_gps_text(text: str) -> GpsTable:
    rows = [r for r in text.splitlines() if r.strip()]
    if not rows:
        raise GpsError("定位文件是空的")
    header = [c.strip() for c in rows[0].split(",")]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise GpsError(f"第一行不是定位表头（缺 {', '.join(missing)}），实际内容：{rows[0][:80]}")
    idx = [header.index(c) for c in REQUIRED]
    width = len(header)
    values = np.empty((len(rows) - 1, len(REQUIRED)), dtype=np.float64)
    for n, raw in enumerate(rows[1:], start=2):
        cells = raw.split(",")
        if len(cells) < width:
            raise GpsError(f"第 {n} 行只有 {len(cells)} 列，表头是 {width} 列")
        try:
            values[n - 2] = [float(cells[i]) for i in idx]
        except ValueError as exc:
            raise GpsError(f"第 {n} 行含有非数字：{raw[:80]}") from exc
    if values.shape[0] < 2:
        raise GpsError(f"只有 {values.shape[0]} 条定位，至少需要 2 条")
    if not np.all(np.isfinite(values)):
        raise GpsError("定位里有 NaN 或无穷大")
    lines = values[:, 0]
    if np.any(lines != np.round(lines)):
        raise GpsError("Lines 列不是整数")
    return GpsTable(
        lines=lines.astype(np.int64),
        time_s=values[:, 1] + values[:, 2] * 1e-6,
        lat=values[:, 3],
        lon=values[:, 4],
        alt=values[:, 5],
    )


def read_gps(path: Path) -> GpsTable:
    size = path.stat().st_size
    if size > GPS_MAX_BYTES:
        raise GpsError(f"定位文件过大（{size} 字节）")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise GpsError("定位文件不是文本")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GpsError("定位文件不是 UTF-8 文本") from exc
    return parse_gps_text(text)
