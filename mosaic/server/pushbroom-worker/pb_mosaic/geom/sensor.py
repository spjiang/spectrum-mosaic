"""线阵相机的地面投影：平地、垂直下视，狭缝垂直于航迹。

一行第 s 个像元落在 P(i) + along·h(i) + sign·(s − c0)·gsd·r(i)：
P 为该行的平面位置，h 为航迹方向，r 为航迹右侧方向，c0 为狭缝中心。
gps 里的姿态四列定义未知，这里不用；起伏地形会带来旁向误差。
狭缝与航迹不垂直（偏流）逐航带由精配准估计，不在这里。

安装参数的取值顺序：任务参数 > 部署配置（环境变量 PB_SENSOR_*）> FS-60C 内置标定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pb_mosaic.ingest.envi import EnviHeader


class SensorError(ValueError):
    pass


@dataclass
class Sensor:
    samples: int
    gsd_cross_m: float
    # 狭缝中心的像元坐标。默认在正中间，boresight_px 可以修正
    center_px: float
    # +1：像元序号向航迹右侧增加；-1：向左侧增加
    sign: int
    # 定位与成像在时间或安装上的错位，折算成沿航向的米数，向前为正
    along_offset_m: float
    flight_height_m: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "gsd_cross_m": round(self.gsd_cross_m, 6),
            "center_px": round(self.center_px, 3),
            "sign": self.sign,
            "along_offset_m": round(self.along_offset_m, 4),
            "flight_height_m": self.flight_height_m,
        }


def cross_gsd_from_header(hdr: EnviHeader, flight_height_m: float | None = None) -> tuple[float, float]:
    """GSD = 航高 × 像元尺寸 × 空间合并 / 焦距。返回 (gsd, 航高)。"""
    height = flight_height_m if flight_height_m is not None else hdr.number("flight height")
    pixel_um = hdr.number("pixel size")
    focal_mm = hdr.number("focal length")
    binning = hdr.number("space binning") or 1.0
    missing = [
        name
        for name, val in (("flight height", height), ("pixel size", pixel_um), ("focal length", focal_mm))
        if not val
    ]
    if missing:
        raise SensorError(f"头文件缺少 {', '.join(missing)}，无法估算地面分辨率，请在参数里指定 gsd_cross_m")
    assert height and pixel_um and focal_mm
    return height * pixel_um * 1e-6 * binning / (focal_mm * 1e-3), float(height)


def deployment_params() -> dict[str, float]:
    """环境变量里的本机标定。写错了直接报错，不悄悄回退到内置值。"""
    out: dict[str, float] = {}
    for key, env in ENV_KEYS.items():
        raw = os.environ.get(env, "").strip()
        if not raw:
            continue
        try:
            out[key] = float(raw)
        except ValueError:
            raise SensorError(f"环境变量 {env}={raw!r} 不是数字") from None
    return out


def sensor_sources(params: dict[str, Any]) -> dict[str, str]:
    """每个安装参数取自哪里：task / deployment / builtin（gsd_cross_m、flight_height_m 缺省时为 header）。"""
    env = deployment_params()
    out = {}
    for key in ENV_KEYS:
        if params.get(key) is not None:
            out[key] = "task"
        elif key in env:
            out[key] = "deployment"
        else:
            out[key] = "header" if key in ("gsd_cross_m", "flight_height_m") else "builtin"
    return out


def _numbers(params: dict[str, Any]) -> dict[str, float]:
    out = {}
    for key in ENV_KEYS:
        val = params.get(key)
        if val is None:
            continue
        if isinstance(val, bool):
            raise SensorError(f"{key} 必须是数字")
        try:
            out[key] = float(val)
        except (TypeError, ValueError):
            raise SensorError(f"{key} 必须是数字，收到 {val!r}") from None
    return out


def sensor_from(hdr: EnviHeader, params: dict[str, Any]) -> Sensor:
    params = {**deployment_params(), **_numbers(params)}
    height = params.get("flight_height_m")
    gsd = params.get("gsd_cross_m")
    if gsd:
        used_height = float(height) if height else hdr.number("flight height")
        gsd_val = float(gsd)
    else:
        gsd_val, used_height = cross_gsd_from_header(hdr, float(height) if height else None)
    if not 0.001 <= gsd_val <= 5.0:
        raise SensorError(f"旁向地面分辨率 {gsd_val:.4f} m 不合理，请检查航高、像元尺寸和焦距")
    raw_sign = params.get("sample_sign", DEFAULT_SAMPLE_SIGN)
    if raw_sign not in (1.0, -1.0):
        raise SensorError("sample_sign 只能是 1 或 -1")
    sign = int(raw_sign)
    return Sensor(
        samples=hdr.samples,
        gsd_cross_m=gsd_val,
        center_px=(hdr.samples - 1) / 2.0 + float(params.get("boresight_px", DEFAULT_BORESIGHT_PX)),
        sign=sign,
        along_offset_m=float(params.get("along_offset_m", DEFAULT_ALONG_OFFSET_M)),
        flight_height_m=used_height,
    )


# FS-60C 的标定值：0419、0429 两个架次用 calibrate 合并得到（1.69 ± 0.11 m，−19.3 ± 3.0 像元），
# 见 README「几何标定」。0414 的狭缝中心约 −50 像元，和之后差 30 像元，判断中间重新安装过。
# 像元方向反了时相邻往返航带会出现约两倍航带间距的旁向错位，很容易识别
DEFAULT_SAMPLE_SIGN = -1
DEFAULT_ALONG_OFFSET_M = 1.7
DEFAULT_BORESIGHT_PX = -19.0

ENV_KEYS = {
    "sample_sign": "PB_SENSOR_SAMPLE_SIGN",
    "along_offset_m": "PB_SENSOR_ALONG_OFFSET_M",
    "boresight_px": "PB_SENSOR_BORESIGHT_PX",
    "gsd_cross_m": "PB_SENSOR_GSD_CROSS_M",
    "flight_height_m": "PB_SENSOR_FLIGHT_HEIGHT_M",
}
