"""传感器安装参数自检：用往返航带的配准平移反推沿航向偏移和狭缝中心。

沿航向偏移设大了 δa，每条航带都沿自己的航向往前错开 δa；狭缝中心设错，
每条航带都向自己航迹的一侧错开 δr。往返航带方向相反，错开方向也相反，
所以能和共同平移、各航带的 GPS 误差分开。配准平移 t_i 是错开的反方向：
  t_i = g − δa·h_i − δr·r_i + ε_i
g 为共同平移，h、r 为航向和航迹右侧单位向量，ε 为各航带 GPS 误差（当作独立同分布）。
两个方向都至少要有一条航带，且总数至少 3 条。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.geom.sensor import Sensor

# 建议值与当前设定相差超过这么多（米），且超过 2 倍标准误差时提示
WARN_M = 0.5
MIN_STRIPS = 3


@dataclass
class CalibrationCheck:
    usable: bool
    strips: int
    note: str
    along_offset_m: float = 0.0
    boresight_px: float = 0.0
    gsd_cross_m: float = 0.0
    along_suggested_m: float | None = None
    along_se_m: float | None = None
    boresight_suggested_px: float | None = None
    boresight_se_px: float | None = None
    gps_scatter_m: float | None = None
    shear_forward_deg: float | None = None
    shear_backward_deg: float | None = None
    warning: str | None = None

    def as_dict(self) -> dict:
        r = lambda v, n=3: None if v is None else round(float(v), n)  # noqa: E731
        return {
            "usable": self.usable,
            "strips": self.strips,
            "note": self.note,
            "along_offset_m": r(self.along_offset_m),
            "boresight_px": r(self.boresight_px, 2),
            "along_suggested_m": r(self.along_suggested_m),
            "along_se_m": r(self.along_se_m),
            "boresight_suggested_px": r(self.boresight_suggested_px, 2),
            "boresight_se_px": r(self.boresight_se_px, 2),
            "gps_scatter_m": r(self.gps_scatter_m),
            "shear_forward_deg": r(self.shear_forward_deg, 2),
            "shear_backward_deg": r(self.shear_backward_deg, 2),
            "warning": self.warning,
        }


def boresight_of(sensor: Sensor) -> float:
    return sensor.center_px - (sensor.samples - 1) / 2.0


def check_calibration(
    geoms: list[StripGeometry],
    offsets: dict[int, tuple[float, float]],
    sensor: Sensor,
    linked: set[int] | None = None,
) -> CalibrationCheck:
    """geoms 带着精配准估计的剪切；offsets 为每条航带的总平移；linked 为参与了配准的航带。"""
    use = [g for g in geoms if linked is None or g.strip.index in linked]
    out = CalibrationCheck(
        usable=False,
        strips=len(use),
        note="",
        along_offset_m=sensor.along_offset_m,
        boresight_px=boresight_of(sensor),
        gsd_cross_m=sensor.gsd_cross_m,
    )
    if not use:
        out.note = "没有参与配准的航带，无法自检"
        return out
    h = np.array([(float(np.median(g.hx)), float(np.median(g.hy))) for g in use])
    h /= np.linalg.norm(h, axis=1, keepdims=True)
    fwd = h @ h[0] >= 0
    shear = np.degrees([g.shear for g in use])
    out.shear_forward_deg = float(np.mean(shear[fwd]))
    if (~fwd).any():
        out.shear_backward_deg = float(np.mean(shear[~fwd]))
    if len(use) < MIN_STRIPS or fwd.all():
        out.note = f"需要至少 {MIN_STRIPS} 条参与配准的往返航带，当前 {len(use)} 条" + ("且只有一个方向" if fwd.all() else "")
        return out
    t = np.array([offsets.get(g.strip.index, (0.0, 0.0)) for g in use], dtype=np.float64)
    rgt = np.column_stack([h[:, 1], -h[:, 0]])
    A = np.zeros((2 * len(use), 4))
    A[0::2, 0], A[1::2, 1] = 1.0, 1.0
    A[0::2, 2], A[1::2, 2] = -h[:, 0], -h[:, 1]
    A[0::2, 3], A[1::2, 3] = -rgt[:, 0], -rgt[:, 1]
    y = t.reshape(-1)
    x, *_ = np.linalg.lstsq(A, y, rcond=None)
    dof = A.shape[0] - A.shape[1]
    rss = float(np.sum((A @ x - y) ** 2))
    sigma = float(np.sqrt(rss / dof))
    cov = np.linalg.pinv(A.T @ A) * sigma**2
    da, dr = float(x[2]), float(x[3])
    se_a, se_r = float(np.sqrt(cov[2, 2])), float(np.sqrt(cov[3, 3]))
    g = sensor.gsd_cross_m
    out.usable = True
    out.gps_scatter_m = sigma
    out.along_suggested_m = sensor.along_offset_m - da
    out.along_se_m = se_a
    out.boresight_suggested_px = out.boresight_px + sensor.sign * dr / g
    out.boresight_se_px = se_r / g
    out.note = (
        f"{len(use)} 条往返航带：沿航向偏移建议 {out.along_suggested_m:.2f} ± {se_a:.2f} m（当前 {sensor.along_offset_m:.2f}），"
        f"狭缝中心建议 {out.boresight_suggested_px:+.0f} ± {out.boresight_se_px:.0f} 像元（当前 {out.boresight_px:+.0f}），"
        f"航带间 GPS 误差约 {sigma:.2f} m"
    )
    far = []
    if abs(da) > max(WARN_M, 2 * se_a):
        far.append(f"沿航向偏移差 {-da:+.2f} m")
    if abs(dr) > max(WARN_M, 2 * se_r):
        far.append(f"狭缝中心差 {sensor.sign * dr / g:+.0f} 像元（{abs(dr):.2f} m）")
    if far:
        out.warning = (
            "传感器标定与本架次不符（" + "，".join(far) + "）：往返航带整体错开，配准已按航带补偿，"
            "但绝对位置会偏。相机重新安装过的话，请用 calibrate 命令复核后更新 PB_SENSOR_* 配置"
        )
    return out


def combine(checks: list[CalibrationCheck], names: list[str] | None = None) -> dict:
    """多个架次按标准误差加权平均。架次之间明显不一致（中途重新安装过）时不给合并值。"""
    names = names or [f"#{k}" for k in range(len(checks))]
    pick = [(n, c) for n, c in zip(names, checks) if c.usable and c.along_se_m and c.boresight_se_px]
    if not pick:
        return {"usable": False, "note": "没有可用于标定的架次（需要往返航带）"}
    ok = [c for _, c in pick]

    def mean(vals: list[float], ses: list[float]) -> tuple[float, float]:
        w = 1.0 / np.square(np.maximum(ses, 1e-3))
        return float(np.sum(w * vals) / np.sum(w)), float(np.sqrt(1.0 / np.sum(w)))

    along = [c.along_suggested_m for c in ok]
    bore = [c.boresight_suggested_px for c in ok]
    a, sa = mean(along, [c.along_se_m for c in ok])  # type: ignore[arg-type,misc]
    b, sb = mean(bore, [c.boresight_se_px for c in ok])  # type: ignore[arg-type,misc]
    out = {
        "usable": True,
        "flights": len(ok),
        "along_offset_m": round(a, 2),
        "along_se_m": round(sa, 2),
        "boresight_px": round(b, 1),
        "boresight_se_px": round(sb, 1),
    }
    bad = []
    for label, vals, ses, centre, unit, to_m in (
        ("沿航向偏移", along, [c.along_se_m for c in ok], a, "m", [1.0] * len(ok)),
        ("狭缝中心", bore, [c.boresight_se_px for c in ok], b, "像元", [c.gsd_cross_m for c in ok]),
    ):
        off = [
            n
            for (n, _), v, s, g in zip(pick, vals, ses, to_m)
            if abs(v - centre) > 3 * s and abs(v - centre) * g > WARN_M  # type: ignore[operator]
        ]
        if off:
            listing = "，".join(f"{n} {v:+.2f}" if unit == "m" else f"{n} {v:+.0f}" for (n, _), v in zip(pick, vals))
            bad.append(f"{label}（{listing} {unit}）")
    if bad:
        out["usable"] = False
        out["note"] = (
            "架次之间不一致：" + "；".join(bad) + "。可能中间重新安装过相机或云台，请只用安装状态相同的架次一起标定"
        )
    return out
