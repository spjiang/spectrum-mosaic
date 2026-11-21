"""合成 FigSpec 三件套：已知地面纹理 + 已知航线，按与 pb_mosaic 相同的平地下视模型逐行成像。

航线（UTM，单位米，时间为整秒折点）：
  北飞 30 s → 东移 5 s（低速，不算航带）→ 南飞 30 s → 东移 5 s → 北飞 30 s
相邻航带旁向重叠约一半，配准有足够重叠面积。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer
from scipy import ndimage

EPSG = 32649
ORIGIN = (230_000.0, 3_890_000.0)
T0 = 1_776_144_944.0
RATE_HZ = 70.0
SAMPLES = 96
GSD = 0.1
SIGN = -1
BANDS = 4
WAVELENGTHS = (460.0, 550.0, 640.0, 800.0)
SPEED = 6.0
STRIP_S = 30
SIDE_S = 5
SPACING_M = 4.0
# 合成时用的传感器参数，跑 pipeline 时原样传入
SENSOR_PARAMS = {"gsd_cross_m": GSD, "sample_sign": SIGN, "boresight_px": 0.0, "along_offset_m": 0.0}

TEX_CELL = 0.25
TEX_MARGIN = 20.0


@dataclass
class Texture:
    """非周期随机地面纹理，值域约 0–1。"""

    x0: float
    y0: float
    data: np.ndarray

    def __call__(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        c = (np.asarray(x) - self.x0) / TEX_CELL
        r = (np.asarray(y) - self.y0) / TEX_CELL
        return ndimage.map_coordinates(self.data, [r, c], order=1, mode="nearest")


def make_texture(seed: int = 7) -> Texture:
    w = int((SPACING_M * 2 + 2 * TEX_MARGIN) / TEX_CELL)
    h = int((SPEED * STRIP_S + 2 * TEX_MARGIN) / TEX_CELL)
    rng = np.random.default_rng(seed)
    fine = ndimage.gaussian_filter(rng.standard_normal((h, w)), 1.0)
    coarse = ndimage.gaussian_filter(rng.standard_normal((h, w)), 6.0)
    d = fine / fine.std() + 0.6 * coarse / coarse.std()
    d = (d - d.min()) / (d.max() - d.min())
    return Texture(ORIGIN[0] - TEX_MARGIN, ORIGIN[1] - TEX_MARGIN, d)


def waypoints() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """折点时间（秒，相对 T0）和 UTM 坐标。"""
    x, y = ORIGIN
    t, xs, ys = [0.0], [x], [y]
    legs = [
        (STRIP_S, 0.0, SPEED),
        (SIDE_S, SPACING_M / SIDE_S, 0.0),
        (STRIP_S, 0.0, -SPEED),
        (SIDE_S, SPACING_M / SIDE_S, 0.0),
        (STRIP_S, 0.0, SPEED),
    ]
    for dur, vx, vy in legs:
        x += vx * dur
        y += vy * dur
        t.append(t[-1] + dur)
        xs.append(x)
        ys.append(y)
    return np.array(t), np.array(xs), np.array(ys)


def position(t: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """真实位置与航向单位向量。"""
    wt, wx, wy = waypoints()
    x = np.interp(t, wt, wx)
    y = np.interp(t, wt, wy)
    leg = np.clip(np.searchsorted(wt, t, side="right") - 1, 0, wt.size - 2)
    dx = np.diff(wx)[leg]
    dy = np.diff(wy)[leg]
    n = np.hypot(dx, dy)
    return x, y, dx / n, dy / n


@dataclass
class SynthFlight:
    root: Path
    texture: Texture
    lines: int


def write_flight(
    root: Path,
    *,
    texture: Texture | None = None,
    bias: tuple[float, float, float, float] | None = None,
    drift: tuple[float, float, float] | None = None,
    shear_deg: tuple[float, float] = (0.0, 0.0),
    sensor: dict | None = None,
    header_extra: str = "",
) -> SynthFlight:
    """bias = (t0, t1, dx, dy)：t0–t1 秒内的定位整体偏 (dx, dy) 米，成像仍按真实位置。

    drift = (t0, t1, north_m)：这段时间中间约一半的定位整段偏北 north_m 米，两端用短斜坡接上。
    成像仍按真实位置。偏东会把窄航带推出重叠区，测不到；沿航向的这段才留在接缝上。
    整段平均值会被航带平移吃掉，剩下的是沿航向变化的那一部分。

    shear_deg = (北飞, 南飞)：狭缝剪切，离中线 d 米的像元沿航向前移 tan(φ)·d 米。
    sensor：成像用的真实安装参数（boresight_px、along_offset_m），默认与 SENSOR_PARAMS 相同。
    """
    root.mkdir(parents=True, exist_ok=True)
    tex = texture or make_texture()
    truth = {**SENSOR_PARAMS, **(sensor or {})}
    wt, _, _ = waypoints()
    duration = float(wt[-1])
    n_lines = int(duration * RATE_HZ) + 1
    t = np.arange(n_lines) / RATE_HZ
    x, y, hx, hy = position(t)
    x = x + truth["along_offset_m"] * hx
    y = y + truth["along_offset_m"] * hy
    s = np.arange(SAMPLES, dtype=np.float64)
    center = (SAMPLES - 1) / 2.0 + truth["boresight_px"]
    d = SIGN * (s - center) * GSD
    phi = np.tan(np.radians(np.where(hy > 0, shear_deg[0], shear_deg[1])))
    a = phi[:, None] * d[None, :]
    gx = x[:, None] + d[None, :] * hy[:, None] + a * hx[:, None]
    gy = y[:, None] - d[None, :] * hx[:, None] + a * hy[:, None]
    v = tex(gx, gy)
    cube = np.empty((n_lines, BANDS, SAMPLES), dtype="<u2")
    for b in range(BANDS):
        cube[:, b, :] = np.clip(500 + 3000 * v * (1.0 + 0.2 * b), 1, 65535).astype("<u2")
    cube.tofile(root / "figspec.spe")

    wl = ",".join(f"{w:g}" for w in WAVELENGTHS)
    (root / "figspec.hdr").write_text(
        "ENVI\n"
        "description = {synthetic}\n"
        f"samples = {SAMPLES}\nlines = {n_lines}\nbands = {BANDS}\n"
        "header offset = 0\nfile type = ENVI Standard\ndata type = 12\ninterleave = bil\n"
        "byte order = 0\ndefault bands = {3,2,1}\n"
        f"wavelength units = Nanometers\nwavelength = {{{wl}}}\n"
        "fx model = FS-60C\nsn = SYNTH\nflight height = 100\n" + header_extra,
        encoding="utf-8",
    )

    rec_t = np.arange(0.0, duration + 1e-9, 1.0)
    rx, ry, _, _ = position(rec_t)
    if bias:
        b0, b1, bx, by = bias
        m = (rec_t >= b0) & (rec_t <= b1)
        rx = rx + np.where(m, bx, 0.0)
        ry = ry + np.where(m, by, 0.0)
    if drift:
        t0, t1, amp = drift
        u = (rec_t - t0) / max(t1 - t0, 1e-6)
        rise = np.clip((u - 0.18) / 0.08, 0.0, 1.0)
        fall = np.clip((0.82 - u) / 0.08, 0.0, 1.0)
        bump = np.where((u >= 0.0) & (u <= 1.0), amp * np.minimum(rise, fall), 0.0)
        ry = ry + bump
    tf = Transformer.from_crs(CRS.from_epsg(EPSG), CRS.from_epsg(4326), always_xy=True)
    lon, lat = tf.transform(rx, ry)
    rows = ["Lines,UnixTime,Microseconds,Latitude,Longitude,Altitude,W,X,Y,Z,XVelocity,YVelocity,ZVelocity"]
    for k, tk in enumerate(rec_t):
        line = int(round(tk * RATE_HZ))
        abs_t = T0 + tk
        sec = int(abs_t)
        usec = int(round((abs_t - sec) * 1e6))
        rows.append(f"{line},{sec},{usec},{lat[k]:.12f},{lon[k]:.12f},100.0,0,0,0,0,0,0,0")
    (root / "figspec.gps").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return SynthFlight(root=root, texture=tex, lines=n_lines)
