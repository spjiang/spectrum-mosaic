"""WGS84 经纬度投到任务所用的 UTM。与 rasterio 对拍误差在毫米级。"""

from __future__ import annotations

import math


def utm_zone_from_epsg(crs: str) -> tuple[int, bool]:
    code = int(str(crs).split(":")[-1])
    if 32601 <= code <= 32660:
        return code - 32600, False
    if 32701 <= code <= 32760:
        return code - 32700, True
    raise ValueError(f"不是 UTM 坐标系: {crs}")


def wgs84_to_utm(lat_deg: float, lon_deg: float, *, zone: int, south: bool) -> tuple[float, float]:
    """Snyder / USGS 横轴墨卡托。zone、south 来自任务 CRS，不按单点重算带号。"""
    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    lon0 = math.radians(zone * 6 - 183)
    e2 = f * (2.0 - f)
    ep2 = e2 / (1.0 - e2)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    tan_lat = math.tan(lat)
    n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    t = tan_lat * tan_lat
    c = ep2 * cos_lat * cos_lat
    aa = (lon - lon0) * cos_lat
    m = a * (
        (1.0 - e2 / 4.0 - 3.0 * e2**2 / 64.0 - 5.0 * e2**3 / 256.0) * lat
        - (3.0 * e2 / 8.0 + 3.0 * e2**2 / 32.0 + 45.0 * e2**3 / 1024.0) * math.sin(2.0 * lat)
        + (15.0 * e2**2 / 256.0 + 45.0 * e2**3 / 1024.0) * math.sin(4.0 * lat)
        - (35.0 * e2**3 / 3072.0) * math.sin(6.0 * lat)
    )
    x = k0 * n * (
        aa
        + (1.0 - t + c) * aa**3 / 6.0
        + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * ep2) * aa**5 / 120.0
    ) + 500000.0
    y = k0 * (
        m
        + n
        * tan_lat
        * (
            aa**2 / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * aa**4 / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * ep2) * aa**6 / 720.0
        )
    )
    if south:
        y += 10000000.0
    return x, y


def utm_to_wgs84(x: float, y: float, *, zone: int, south: bool) -> tuple[float, float]:
    """上面正算的反算。返回 (lat, lon)，单位度。"""
    a = 6378137.0
    f = 1.0 / 298.257223563
    k0 = 0.9996
    e2 = f * (2.0 - f)
    ep2 = e2 / (1.0 - e2)
    if south:
        y -= 10000000.0
    x -= 500000.0
    lon0 = math.radians(zone * 6 - 183)
    m = y / k0
    mu = m / (a * (1.0 - e2 / 4.0 - 3.0 * e2**2 / 64.0 - 5.0 * e2**3 / 256.0))
    e1 = (1.0 - math.sqrt(1.0 - e2)) / (1.0 + math.sqrt(1.0 - e2))
    phi = (
        mu
        + (3.0 * e1 / 2.0 - 27.0 * e1**3 / 32.0) * math.sin(2.0 * mu)
        + (21.0 * e1**2 / 16.0 - 55.0 * e1**4 / 32.0) * math.sin(4.0 * mu)
        + (151.0 * e1**3 / 96.0) * math.sin(6.0 * mu)
        + (1097.0 * e1**4 / 512.0) * math.sin(8.0 * mu)
    )
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    tan_phi = math.tan(phi)
    n = a / math.sqrt(1.0 - e2 * sin_phi * sin_phi)
    t = tan_phi * tan_phi
    c = ep2 * cos_phi * cos_phi
    r = a * (1.0 - e2) / (1.0 - e2 * sin_phi * sin_phi) ** 1.5
    d = x / (n * k0)
    lat = phi - (n * tan_phi / r) * (
        d**2 / 2.0
        - (5.0 + 3.0 * t + 10.0 * c - 4.0 * c * c - 9.0 * ep2) * d**4 / 24.0
        + (61.0 + 90.0 * t + 298.0 * c + 45.0 * t * t - 252.0 * ep2 - 3.0 * c * c) * d**6 / 720.0
    )
    lon = lon0 + (
        d
        - (1.0 + 2.0 * t + c) * d**3 / 6.0
        + (5.0 - 2.0 * c + 28.0 * t - 3.0 * c * c + 8.0 * ep2 + 24.0 * t * t) * d**5 / 120.0
    ) / cos_phi
    return math.degrees(lat), math.degrees(lon)
