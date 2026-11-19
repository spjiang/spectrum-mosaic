"""成果立方体的 ENVI 头：UTM 地理信息、坐标系、所选波段的波长，原始传感器字段照抄。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from pyproj import CRS

from pb_mosaic.geom.grid import OutputGrid
from pb_mosaic.ingest.envi import EnviHeader, write_header
from pb_mosaic.ingest.flight import SENSOR_FIELDS
from pb_mosaic.mosaic.merge import NODATA


def map_info(grid: OutputGrid) -> list:
    """ENVI map info：参考像元 (1, 1) 是左上像元的左上角。"""
    return [
        "UTM",
        1,
        1,
        float(grid.x0),
        float(grid.y0),
        float(grid.gsd),
        float(grid.gsd),
        grid.zone,
        "North" if grid.north else "South",
        "WGS-84",
        "units=Meters",
    ]


def coordinate_system_string(epsg: int) -> str:
    return "{" + CRS.from_epsg(epsg).to_wkt("WKT1_ESRI") + "}"


def _pick(values: list, band_idx: np.ndarray | None) -> list | None:
    if not values:
        return None
    if band_idx is None:
        return list(values)
    return [values[int(i)] for i in band_idx]


def default_bands_out(hdr: EnviHeader, band_idx: np.ndarray | None) -> list[int] | None:
    """原始 default bands（1 起始）换成成果里的序号；有任何一个不在成果里就不写。"""
    if not hdr.default_bands:
        return None
    if band_idx is None:
        return list(hdr.default_bands)
    pos = {int(b): k for k, b in enumerate(band_idx)}
    mapped = [pos.get(b - 1) for b in hdr.default_bands]
    if any(m is None for m in mapped):
        return None
    return [int(m) + 1 for m in mapped]  # type: ignore[operator]


def write_mosaic_header(
    path: Path,
    hdr: EnviHeader,
    grid: OutputGrid,
    band_idx: np.ndarray | None,
    *,
    description: str,
) -> None:
    nb = hdr.bands if band_idx is None else int(band_idx.size)
    raw_band_names = hdr.fields.get("band names")
    fields: list[tuple[str, object]] = [
        ("description", "{" + description.replace("{", "(").replace("}", ")") + "}"),
        ("samples", grid.width),
        ("lines", grid.height),
        ("bands", nb),
        ("header offset", 0),
        ("file type", "ENVI Standard"),
        ("data type", hdr.data_type),
        ("interleave", "bil"),
        ("byte order", 0),
        ("map info", map_info(grid)),
        ("coordinate system string", coordinate_system_string(grid.epsg)),
        ("data ignore value", NODATA),
        ("default bands", default_bands_out(hdr, band_idx)),
        ("wavelength units", hdr.fields.get("wavelength units") or ("Nanometers" if hdr.wavelengths else None)),
        ("wavelength", _pick(hdr.wavelengths, band_idx)),
        ("fwhm", _pick(hdr.fwhm, band_idx)),
        ("band names", raw_band_names if band_idx is None else None),
    ]
    # 传感器字段保留原文（可能带花括号），供下游做辐射定标
    for key in SENSOR_FIELDS:
        if key in hdr.fields:
            fields.append((key, hdr.fields[key]))
    if band_idx is not None:
        fields.append(("source bands", [int(i) + 1 for i in band_idx]))
    write_header(path, fields)
