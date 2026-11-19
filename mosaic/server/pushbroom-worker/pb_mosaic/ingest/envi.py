"""ENVI 头文件读写与 BIL 立方体的只读映射。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ENVI data type → numpy。目前只接 12（uint16），其它类型给出明确报错
SUPPORTED_DTYPES = {12: np.dtype("<u2")}
HDR_MAX_BYTES = 1 << 20


class EnviError(ValueError):
    pass


def parse_header_text(text: str) -> dict[str, str]:
    """key = value，花括号里的值可以跨行。键统一小写，值保留原文。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "ENVI":
        raise EnviError("第一行不是 ENVI")
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for raw in lines[1:]:
        if key is not None:
            buf.append(raw)
            if "}" in raw:
                out[key] = " ".join(buf).strip()
                key, buf = None, []
            continue
        if "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k = k.strip().lower()
        v = v.strip()
        if v.startswith("{") and "}" not in v:
            key, buf = k, [v]
            continue
        out[k] = v
    if key is not None:
        raise EnviError(f"字段 {key} 的花括号没有闭合")
    return out


def envi_list(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return [x.strip() for x in text.split(",") if x.strip()]


def _int(fields: dict[str, str], key: str, default: int | None = None) -> int:
    raw = fields.get(key)
    if raw is None:
        if default is not None:
            return default
        raise EnviError(f"头文件缺少 {key}")
    try:
        return int(raw)
    except ValueError as exc:
        raise EnviError(f"头文件 {key} 不是整数: {raw}") from exc


def _float_or_none(fields: dict[str, str], key: str) -> float | None:
    raw = fields.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class EnviHeader:
    samples: int
    lines: int
    bands: int
    data_type: int
    interleave: str
    byte_order: int
    header_offset: int
    wavelengths: list[float] = field(default_factory=list)
    fwhm: list[float] = field(default_factory=list)
    # ENVI 的 default bands 是 1 起始
    default_bands: list[int] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def dtype(self) -> np.dtype:
        return SUPPORTED_DTYPES[self.data_type]

    @property
    def expected_bytes(self) -> int:
        return self.header_offset + self.samples * self.lines * self.bands * self.dtype.itemsize

    def number(self, key: str) -> float | None:
        return _float_or_none(self.fields, key)


def parse_header(text: str) -> EnviHeader:
    f = parse_header_text(text)
    hdr = EnviHeader(
        samples=_int(f, "samples"),
        lines=_int(f, "lines"),
        bands=_int(f, "bands"),
        data_type=_int(f, "data type"),
        interleave=f.get("interleave", "").strip().lower(),
        byte_order=_int(f, "byte order", 0),
        header_offset=_int(f, "header offset", 0),
        fields=f,
    )
    for name, val in (("samples", hdr.samples), ("lines", hdr.lines), ("bands", hdr.bands)):
        if val <= 0:
            raise EnviError(f"头文件 {name} 必须大于 0")
    if hdr.interleave != "bil":
        raise EnviError(f"只支持 BIL 交错，当前是 {hdr.interleave or '未填写'}")
    if hdr.byte_order != 0:
        raise EnviError(f"只支持小端字节序（byte order = 0），当前是 {hdr.byte_order}")
    if hdr.data_type not in SUPPORTED_DTYPES:
        raise EnviError(f"只支持 data type = 12（uint16），当前是 {hdr.data_type}")
    try:
        if "wavelength" in f:
            hdr.wavelengths = [float(x) for x in envi_list(f["wavelength"])]
        if "fwhm" in f:
            hdr.fwhm = [float(x) for x in envi_list(f["fwhm"])]
        if "default bands" in f:
            hdr.default_bands = [int(float(x)) for x in envi_list(f["default bands"])]
    except ValueError as exc:
        raise EnviError(f"头文件波长或默认波段含有非数字：{exc}") from exc
    if hdr.wavelengths and len(hdr.wavelengths) != hdr.bands:
        raise EnviError(f"wavelength 有 {len(hdr.wavelengths)} 个，与 bands = {hdr.bands} 不一致")
    if hdr.fwhm and len(hdr.fwhm) != hdr.bands:
        hdr.fwhm = []
    hdr.default_bands = [b for b in hdr.default_bands if 1 <= b <= hdr.bands]
    return hdr


def read_header(path: Path) -> EnviHeader:
    size = path.stat().st_size
    if size > HDR_MAX_BYTES:
        raise EnviError(f"头文件过大（{size} 字节）")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise EnviError("头文件不是文本，可能是 macOS 拷贝留下的 ._ 资源文件")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EnviError("头文件不是 UTF-8 文本") from exc
    return parse_header(text)


def open_bil(path: Path, hdr: EnviHeader) -> np.memmap:
    """只读映射成 (lines, bands, samples)。按行读是连续的。"""
    return np.memmap(
        path,
        dtype=hdr.dtype,
        mode="r",
        offset=hdr.header_offset,
        shape=(hdr.lines, hdr.bands, hdr.samples),
    )


def _fmt_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "{" + ", ".join(_fmt_scalar(v) for v in value) + "}"
    return _fmt_scalar(value)


def _fmt_scalar(value: Any) -> str:
    if isinstance(value, float):
        return repr(round(value, 10))
    return str(value)


def write_header(path: Path, fields: list[tuple[str, Any]]) -> None:
    """按给定顺序写 ENVI 头。值为列表时写成花括号。"""
    lines = ["ENVI"]
    for key, value in fields:
        if value is None:
            continue
        lines.append(f"{key} = {_fmt_value(value)}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
