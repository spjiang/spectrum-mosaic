from __future__ import annotations

import numpy as np
import pytest
from synth import RATE_HZ, SENSOR_PARAMS, SPEED, STRIP_S

from pb_mosaic.geom.footprint import strip_geometry
from pb_mosaic.geom.grid import default_gsd, grid_from_bounds
from pb_mosaic.geom.line_clock import fit_line_clock
from pb_mosaic.geom.sensor import SensorError, cross_gsd_from_header, sensor_from
from pb_mosaic.geom.strips import split_strips
from pb_mosaic.geom.trajectory import build_trajectory, utm_epsg
from pb_mosaic.ingest.envi import parse_header
from pb_mosaic.ingest.flight import check_flight
from pb_mosaic.ingest.gps import GpsTable

T0 = 1_776_144_944.0


def _logger_gps(rate: float = 69.99, period: float = 1.037, n: int = 300, pause: tuple[float, float] | None = None) -> GpsTable:
    """模拟 FS-60C 记录器：每 period 秒记一条，行计数每 7 行更新，定位取最近一个整秒。"""
    rec = np.arange(n) * period + 0.35
    if pause:
        # 相机在 pause 时段停拍：之后的行号整体少了这段时间
        p0, p1 = pause
        cam_t = np.where(rec < p0, rec, np.where(rec < p1, p0, rec - (p1 - p0)))
    else:
        cam_t = rec
    lines = (np.floor(cam_t * rate / 7) * 7).astype(np.int64)
    fix = np.floor(rec)
    keep = np.append(True, np.diff(lines) > 0)
    return GpsTable(
        lines=lines[keep],
        time_s=T0 + fix[keep],
        lat=np.full(keep.sum(), 35.0),
        lon=np.full(keep.sum(), 113.0),
        alt=np.full(keep.sum(), 100.0),
    )


def test_line_clock_removes_logger_sawtooth() -> None:
    gps = _logger_gps()
    clock = fit_line_clock(gps)
    assert clock.method == "linear"
    assert clock.rate_hz == pytest.approx(69.99, abs=0.02)
    assert 0.8 < clock.age_span_s < 1.2
    lines = np.arange(0, int(gps.lines[-1]), 97, dtype=np.float64)
    true_t = T0 + lines / 69.99
    # 逐条记录插值会有 0–1 s 锯齿；线性时钟只剩 7 行打包的量化误差
    assert np.max(np.abs(clock.time(lines) - true_t)) < 0.15
    naive = np.interp(lines, gps.lines, gps.time_s)
    assert np.max(np.abs(naive - true_t)) > 0.5
    # 绝对时间约 1.7e9 秒，双精度往返误差在 1e-4 行量级
    assert np.allclose(clock.lines_at(clock.time(lines)), lines, rtol=0, atol=1e-3)


def test_line_clock_falls_back_when_camera_paused() -> None:
    clock = fit_line_clock(_logger_gps(pause=(100.0, 110.0)))
    assert clock.method == "records"
    assert "逐条记录" in clock.note


def test_line_clock_few_records() -> None:
    gps = _logger_gps(n=5)
    assert fit_line_clock(gps).method == "records"


def test_utm_zone() -> None:
    assert utm_epsg(113.76, 35.13) == 32649
    assert utm_epsg(-70.0, -33.0) == 32719


def test_strips_from_synthetic_flight(synth_flight) -> None:
    flight = check_flight(synth_flight.root)
    traj = build_trajectory(flight.gps)
    assert traj.epsg == 32649
    strips = split_strips(traj)
    assert [round(s.heading_deg) % 360 for s in strips] == [0, 180, 0]
    leg_lines = STRIP_S * RATE_HZ
    for s in strips:
        assert s.speed_mps == pytest.approx(SPEED, rel=0.01)
        assert s.length_m > 30
        assert s.lines < leg_lines
    # 第二条航带完全落在南飞那段里（转弯、侧移都被裁掉）
    south0 = (STRIP_S + 5) * RATE_HZ
    assert south0 < strips[1].line_start < strips[1].line_end < south0 + leg_lines


def test_footprint_invert_roundtrip(synth_flight) -> None:
    flight = check_flight(synth_flight.root)
    traj = build_trajectory(flight.gps)
    strip = split_strips(traj)[1]
    sensor = sensor_from(flight.hdr, SENSOR_PARAMS)
    g = strip_geometry(traj, strip, sensor)
    rng = np.random.default_rng(1)
    i = rng.uniform(0, g.n - 1, 500)
    s = rng.uniform(0, sensor.samples - 1, 500)
    x, y = g.ground(i, s)
    i2, s2, ok = g.invert(x, y)
    assert ok.all()
    assert np.max(np.abs(i2 - i)) < 0.01
    assert np.max(np.abs(s2 - s)) < 0.01
    # 航带外的点判为不在
    _, _, ok_out = g.invert(x + 100.0, y)
    assert not ok_out.any()


def test_translated_moves_ground(synth_flight) -> None:
    flight = check_flight(synth_flight.root)
    traj = build_trajectory(flight.gps)
    g = strip_geometry(traj, split_strips(traj)[0], sensor_from(flight.hdr, SENSOR_PARAMS))
    x, y = g.ground(np.array([10.0]), np.array([5.0]))
    g.invert(x, y)  # 先建好查表缓存，平移后必须失效
    moved = g.translated(0.7, -0.3)
    x2, y2 = moved.ground(np.array([10.0]), np.array([5.0]))
    assert (x2 - x)[0] == pytest.approx(0.7) and (y2 - y)[0] == pytest.approx(-0.3)
    i, s, ok = moved.invert(x2, y2)
    assert ok[0] and i[0] == pytest.approx(10.0, abs=0.01) and s[0] == pytest.approx(5.0, abs=0.01)
    assert g.translated(0.0, 0.0) is g


HDR = """ENVI
samples = 960
lines = 10
bands = 2
data type = 12
interleave = bil
flight height = 100
pixel size = 5.86
focal length = 25
space binning = 2
"""


def test_cross_gsd_from_header() -> None:
    gsd, h = cross_gsd_from_header(parse_header(HDR))
    assert h == 100
    assert gsd == pytest.approx(100 * 5.86e-6 * 2 / 0.025)


def test_sensor_defaults_and_overrides() -> None:
    hdr = parse_header(HDR)
    s = sensor_from(hdr, {})
    assert s.sign == -1
    assert s.center_px == pytest.approx(479.5 - 49.0)
    assert s.along_offset_m == 2.0
    s2 = sensor_from(hdr, {"gsd_cross_m": 0.05, "sample_sign": 1, "boresight_px": 0, "along_offset_m": 0})
    assert (s2.gsd_cross_m, s2.sign, s2.center_px, s2.along_offset_m) == (0.05, 1, 479.5, 0.0)


@pytest.mark.parametrize(
    ("hdr", "params", "message"),
    [
        (HDR.replace("pixel size = 5.86\n", ""), {}, "pixel size"),
        (HDR, {"sample_sign": 2}, "sample_sign"),
        (HDR, {"gsd_cross_m": 50}, "不合理"),
    ],
)
def test_sensor_errors(hdr: str, params: dict, message: str) -> None:
    with pytest.raises(SensorError, match=message):
        sensor_from(parse_header(hdr), params)


def test_grid_alignment() -> None:
    g = grid_from_bounds(32649, 100.03, 200.01, 110.0, 205.0, 0.1)
    assert g.x0 == pytest.approx(100.0)
    assert g.y0 == pytest.approx(205.0)
    assert g.width == 100 and g.height == 50
    assert default_gsd(0.0375, 0.071) == 0.071
