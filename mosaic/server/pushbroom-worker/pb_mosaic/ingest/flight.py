"""一个架次：figspec.hdr / .spe / .gps 三件套的完整核对。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pb_mosaic.ingest.envi import EnviError, EnviHeader, open_bil, read_header
from pb_mosaic.ingest.gps import GpsError, GpsTable, read_gps

HDR_NAME = "figspec.hdr"
SPE_NAME = "figspec.spe"
GPS_NAME = "figspec.gps"

# 相邻两条定位隔得太久，中间的行只能靠外推，几何不可信
MAX_GPS_GAP_S = 5.0


class FlightError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("；".join(errors))
        self.errors = errors


@dataclass
class Flight:
    root: Path
    hdr: EnviHeader
    gps: GpsTable
    line_rate_hz: float
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def spe_path(self) -> Path:
        return self.root / SPE_NAME

    def open_cube(self) -> np.memmap:
        return open_bil(self.spe_path, self.hdr)

    def summary(self) -> dict:
        return {
            "name": self.name,
            "samples": self.hdr.samples,
            "lines": self.hdr.lines,
            "bands": self.hdr.bands,
            "wavelength_min": min(self.hdr.wavelengths) if self.hdr.wavelengths else None,
            "wavelength_max": max(self.hdr.wavelengths) if self.hdr.wavelengths else None,
            "gps_records": len(self.gps),
            "gps_first_line": int(self.gps.lines[0]),
            "gps_last_line": int(self.gps.lines[-1]),
            "duration_s": round(float(self.gps.time_s[-1] - self.gps.time_s[0]), 3),
            "line_rate_hz": round(self.line_rate_hz, 3),
            "sensor": {k: self.hdr.fields.get(k) for k in SENSOR_FIELDS if k in self.hdr.fields},
        }


# 原样带到成果头文件里的传感器字段
SENSOR_FIELDS = (
    "fx model",
    "sn",
    "lens adjust mode",
    "grab method",
    "flight height",
    "pixel size",
    "space binning",
    "spectrum binning",
    "focal length",
    "exposure time",
    "gain",
    "deducting dark current",
)


def _check_gps(gps: GpsTable, hdr: EnviHeader, errors: list[str], warnings: list[str]) -> float:
    dl = np.diff(gps.lines)
    dt = np.diff(gps.time_s)
    if np.any(dl <= 0):
        bad = int(np.argmax(dl <= 0)) + 2
        errors.append(f"定位第 {bad} 条的行号没有递增")
    if np.any(dt <= 0):
        bad = int(np.argmax(dt <= 0)) + 2
        errors.append(f"定位第 {bad} 条的时间没有递增")
    if gps.lines[0] < 0 or gps.lines[-1] >= hdr.lines:
        errors.append(f"定位行号 {gps.lines[0]}–{gps.lines[-1]} 超出立方体 0–{hdr.lines - 1}")
    if np.any(np.abs(gps.lat) > 90) or np.any(np.abs(gps.lon) > 180):
        errors.append("定位经纬度超出范围")
    elif np.any((gps.lat == 0) & (gps.lon == 0)):
        errors.append("定位里有 0,0 的无效坐标")
    if errors:
        return 0.0
    gaps = dt > MAX_GPS_GAP_S
    if np.any(gaps):
        warnings.append(f"定位有 {int(gaps.sum())} 处间隔超过 {MAX_GPS_GAP_S:g} 秒，这些时段的行不参与拼图")
    rate = float(np.median(dl / dt))
    if not np.isfinite(rate) or rate <= 0:
        errors.append("无法从定位算出扫描行频")
        return 0.0
    head = int(gps.lines[0])
    tail = hdr.lines - 1 - int(gps.lines[-1])
    if head or tail:
        warnings.append(f"首尾共 {head + tail} 行没有定位覆盖，不参与拼图")
    return rate


def check_flight(root: Path) -> Flight:
    root = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        raise FlightError(["架次目录不存在"])
    missing = [n for n in (HDR_NAME, SPE_NAME, GPS_NAME) if not (root / n).is_file()]
    if missing:
        raise FlightError([f"缺少文件：{', '.join(missing)}"])
    hdr: EnviHeader | None = None
    gps: GpsTable | None = None
    try:
        hdr = read_header(root / HDR_NAME)
    except (EnviError, OSError) as exc:
        errors.append(f"{HDR_NAME} 无效：{exc}")
    try:
        gps = read_gps(root / GPS_NAME)
    except (GpsError, OSError) as exc:
        errors.append(f"{GPS_NAME} 无效：{exc}")
    if hdr is not None:
        actual = (root / SPE_NAME).stat().st_size
        if actual != hdr.expected_bytes:
            errors.append(f"{SPE_NAME} 大小 {actual} 字节，按头文件应为 {hdr.expected_bytes} 字节")
        if not hdr.wavelengths:
            warnings.append("头文件没有 wavelength，成果里不带波长")
    if errors or hdr is None or gps is None:
        raise FlightError(errors)
    rate = _check_gps(gps, hdr, errors, warnings)
    if errors:
        raise FlightError(errors)
    return Flight(root=root, hdr=hdr, gps=gps, line_rate_hz=rate, warnings=warnings)
