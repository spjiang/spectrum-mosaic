"""特征点沿航向配准：平移恢复，以及合成架次上随航向变化的 GPS 漂移。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage
from synth import SIDE_S, STRIP_S, SENSOR_PARAMS, Texture, make_texture, write_flight
from test_pipeline import _load, _truth_corr

from pb_mosaic.geom.sensor import Sensor
from pb_mosaic.geom.strips import Strip
from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.mosaic.tiepoints import (
    MIN_BOW_M,
    MIN_MEDIAN_M,
    Obs,
    TieParams,
    _pair_obs,
    match_translation,
    seam_bow_m,
    solve_bends,
)
from pb_mosaic.pipeline import (
    JobParams,
    LogReporter,
    _Stage,
    _align_band,
    build_trajectory,
    check_flight,
    run,
    sensor_from,
    solve_geometry,
    split_strips,
)


def test_match_translation_recovers_a_shift() -> None:
    rng = np.random.default_rng(1)
    base = ndimage.gaussian_filter(rng.normal(size=(180, 260)), 1.1)
    dcol, drow = 6, -3
    ref = base[30:150, 40:200]
    tpl = base[30 + drow : 150 + drow, 40 + dcol : 200 + dcol]
    ok = np.ones(ref.shape, dtype=bool)
    ok[:2, :] = ok[-2:, :] = ok[:, :2] = ok[:, -2:] = False
    got = match_translation(ref, tpl, ok, ok, max_shift_px=20)
    assert got is not None
    assert got[0] == pytest.approx(dcol, abs=0.6)
    assert got[1] == pytest.approx(drow, abs=0.6)
    assert got[2] >= 8


def _strip(index: int, n: int, step_m: float) -> StripGeometry:
    step = np.full(n, step_m)
    return StripGeometry(
        strip=Strip(index, 0, n, 0.0, n * step_m, 6.0, n * step_m / 6.0),
        sensor=Sensor(samples=96, gsd_cross_m=0.1, center_px=47.5, sign=-1, along_offset_m=0.0),
        px=np.zeros(n),
        py=np.cumsum(step),
        hx=np.zeros(n),
        hy=np.ones(n),
        step=step,
    )


def test_solve_bends_follows_a_ramp() -> None:
    # 航带 1 相对航带 0 的东向误差随里程线性变化，北向没有误差
    us = np.linspace(15.0, 165.0, 16)
    obs = [Obs(0, 1, float(u), float(u), 0.012 * float(u) - 1.08, 0.0, 24) for u in us]
    solved, med, hi = solve_bends(obs, {0: _strip(0, 1800, 0.1), 1: _strip(1, 1800, 0.1)})
    assert med < 0.08
    assert hi < 0.15

    def east_at(strip: int, u: float) -> float:
        line = int(round(u / 0.1))
        return float(solved[strip][0][line])

    # 零和约束把改正分到两条航带上，两条之差才是观测到的错位
    gap_lo = east_at(1, 30.0) - east_at(0, 30.0)
    gap_hi = east_at(1, 150.0) - east_at(0, 150.0)
    assert gap_lo == pytest.approx(0.012 * 30 - 1.08, abs=0.12)
    assert gap_hi == pytest.approx(0.012 * 150 - 1.08, abs=0.12)
    assert gap_hi - gap_lo == pytest.approx(0.012 * 120, abs=0.15)


def test_sign_changing_bow_is_not_treated_as_aligned() -> None:
    # 真实接缝：中间过零，两端大约半米。错位的中位数不到旧门槛，摆幅还在。
    us = np.linspace(7.0, 188.0, 14)
    east = np.array(
        [0.49, 0.37, 0.21, 0.06, -0.04, -0.11, -0.19, -0.19, -0.18, -0.09, 0.0, 0.01, 0.10, 0.14]
    )
    obs = [Obs(0, 1, float(u), 195.0 - float(u), float(e), 0.0, 70) for u, e in zip(us, east)]
    assert float(np.median([o.size for o in obs])) < MIN_MEDIAN_M
    assert seam_bow_m(obs) >= 0.40
    solved, med, hi = solve_bends(obs, {0: _strip(0, 5400, 0.037), 1: _strip(1, 5400, 0.037)})
    assert med < 0.08
    assert hi < 0.15
    assert abs(float(solved[0][0].max())) < 0.05

    def east_at(u: float) -> float:
        return float(solved[1][0][int(round(u / 0.037))])

    assert east_at(195.0 - 7.0) == pytest.approx(0.49, abs=0.15)
    assert east_at(195.0 - 91.0) == pytest.approx(-0.19, abs=0.12)


def test_small_jitter_has_no_bow() -> None:
    us = np.linspace(7.0, 188.0, 14)
    jitter = [0.08, -0.05, 0.04, -0.07, 0.02, -0.03, 0.06, -0.04, 0.01, -0.08, 0.05, -0.02, 0.03, -0.06]
    obs = [Obs(0, 1, float(u), float(u), float(e), float(e) / 2, 40) for u, e in zip(us, jitter)]
    assert seam_bow_m(obs) < MIN_BOW_M


def _blocky_texture() -> Texture:
    """随机矩形，边长大约 1–2 米。平滑噪声上提不出特征点，重复色块又会对错。"""
    base = make_texture(11)
    data = base.data.copy()
    h, w = data.shape
    rng = np.random.default_rng(3)
    for _ in range(500):
        rh = int(rng.integers(4, 12))
        rw = int(rng.integers(4, 12))
        r = int(rng.integers(0, h - rh))
        c = int(rng.integers(0, w - rw))
        data[r : r + rh, c : c + rw] = float(rng.random())
    return Texture(base.x0, base.y0, data)


def _remaining_north(root: Path) -> list[float]:
    """弯曲写回几何之后，重叠区里还能量到的南北错位。"""
    fl = check_flight(root)
    traj = build_trajectory(fl.gps)
    strips = split_strips(traj)
    params = JobParams.from_dict(SENSOR_PARAMS)
    geo = solve_geometry(
        fl, traj, strips, sensor_from(fl.hdr, params.sensor), params, _Stage(LogReporter(), {}, "P3_align"), None
    )
    cube = fl.open_cube()
    band = _align_band(fl)
    cache: dict[int, np.ndarray] = {}

    def plane(g: StripGeometry) -> np.ndarray:
        if g.strip.index not in cache:
            cache[g.strip.index] = np.asarray(cube[g.strip.line_start : g.strip.line_end, band, :], dtype=np.float32)
        return cache[g.strip.index]

    shifts: list[float] = []
    for i in range(len(geo.geoms)):
        for j in range(i + 1, len(geo.geoms)):
            shifts.extend(o.north for o in _pair_obs(geo.geoms[i], geo.geoms[j], plane, traj.epsg, TieParams()))
    return shifts


def test_tiepoints_follow_along_track_drift(tmp_path: Path) -> None:
    t0 = STRIP_S + SIDE_S
    flight = write_flight(
        tmp_path / "in" / "FigSpec_DRIFT", texture=_blocky_texture(), drift=(t0, t0 + STRIP_S, 2.2)
    )
    result = run(flight.root, tmp_path / "out", SENSOR_PARAMS)
    tie = result.report["alignment"]["tiepoints"]
    assert tie["applied"], tie
    # 改正落在被拉偏的那一条上，第一条航带保持不动
    assert float(tie["span_m"]["1"]) >= 1.5
    assert float(tie["span_m"]["0"]) < 0.05
    assert tie["median_after_m"] < 0.25
    shifts = _remaining_north(flight.root)
    assert shifts, "弯曲之后重叠区应该还能配上特征点"
    assert float(np.median(np.abs(shifts))) < 0.25
    assert float(np.max(np.abs(shifts))) < 1.0
    off = {int(k): v for k, v in result.report["alignment"]["offsets_east_north_m"].items()}
    cube = _load(tmp_path / "out", result.report)
    corr = _truth_corr(flight, cube, result.report, dx=-off[0][0], dy=-off[0][1])
    # 色块纹理本身有重采样误差，对齐良好时相关性在 0.92 附近，而不是平滑纹理的 0.99
    assert corr > 0.90
    md = (tmp_path / "out" / "report" / "report.md").read_text(encoding="utf-8")
    assert "### 特征点" in md
