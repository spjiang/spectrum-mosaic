"""推扫架次的提交前核对。

只读 .hdr 和 .gps 文本、比对 .spe 字节数，不读影像本体，几十 GB 的架次也在毫秒级完成。
逐行时间、航带切分等完整核对在推扫 worker 的第一步做。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HDR_NAME = "figspec.hdr"
SPE_NAME = "figspec.spe"
GPS_NAME = "figspec.gps"

# ENVI data type 12 = uint16
_BYTES_PER_SAMPLE = {12: 2}
_HDR_MAX_BYTES = 1 << 20
_GPS_MAX_BYTES = 64 << 20
GPS_REQUIRED_COLUMNS = ("Lines", "UnixTime", "Microseconds", "Latitude", "Longitude", "Altitude")


@dataclass
class FlightCheck:
    input_dir: str
    name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flight: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_dir": self.input_dir,
            "name": self.name,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "flight": dict(self.flight),
        }


def parse_envi_header(text: str) -> dict[str, str]:
    """ENVI 头是 key = value，花括号里的值可以跨行。键统一小写。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "ENVI":
        raise ValueError("第一行不是 ENVI")
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
        raise ValueError(f"字段 {key} 的花括号没有闭合")
    return out


def envi_list(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return [x.strip() for x in text.split(",") if x.strip()]


def _int_field(hdr: dict[str, str], key: str, errors: list[str]) -> int | None:
    raw = hdr.get(key)
    if raw is None:
        errors.append(f"{HDR_NAME} 缺少 {key}")
        return None
    try:
        val = int(raw)
    except ValueError:
        errors.append(f"{HDR_NAME} 的 {key} 不是整数: {raw}")
        return None
    return val


def _read_text(path: Path, limit: int) -> str:
    size = path.stat().st_size
    if size > limit:
        raise ValueError(f"文件过大（{size} 字节）")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("不是文本文件")
    return data.decode("utf-8-sig")


def _check_header(path: Path, result: FlightCheck) -> dict[str, str] | None:
    try:
        hdr = parse_envi_header(_read_text(path, _HDR_MAX_BYTES))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        result.errors.append(f"{HDR_NAME} 不是有效的 ENVI 头文件：{exc}")
        return None
    return hdr


def _check_layout(hdr: dict[str, str], spe: Path, result: FlightCheck) -> None:
    errs = result.errors
    samples = _int_field(hdr, "samples", errs)
    lines = _int_field(hdr, "lines", errs)
    bands = _int_field(hdr, "bands", errs)
    dtype = _int_field(hdr, "data type", errs)
    offset = int(hdr.get("header offset", "0") or 0)
    interleave = hdr.get("interleave", "").strip().lower()
    byte_order = hdr.get("byte order", "0").strip()
    if interleave != "bil":
        errs.append(f"只支持 BIL 交错，当前是 {interleave or '未填写'}")
    if byte_order != "0":
        errs.append(f"只支持小端字节序（byte order = 0），当前是 {byte_order}")
    if dtype is not None and dtype not in _BYTES_PER_SAMPLE:
        errs.append(f"只支持 data type = 12（uint16），当前是 {dtype}")
    for name, val in (("samples", samples), ("lines", lines), ("bands", bands)):
        if val is not None and val <= 0:
            errs.append(f"{HDR_NAME} 的 {name} 必须大于 0")
    wavelengths: list[float] = []
    if "wavelength" in hdr:
        try:
            wavelengths = [float(x) for x in envi_list(hdr["wavelength"])]
        except ValueError:
            errs.append(f"{HDR_NAME} 的 wavelength 含有非数字")
        if wavelengths and bands is not None and len(wavelengths) != bands:
            errs.append(f"wavelength 有 {len(wavelengths)} 个，与 bands = {bands} 不一致")
    else:
        result.warnings.append(f"{HDR_NAME} 没有 wavelength，成果里不带波长")
    if errs or samples is None or lines is None or bands is None or dtype is None:
        return
    expected = offset + samples * lines * bands * _BYTES_PER_SAMPLE[dtype]
    actual = spe.stat().st_size
    if actual != expected:
        errs.append(
            f"{SPE_NAME} 大小 {actual} 字节，按头文件应为 {expected} 字节"
            f"（{samples}×{lines}×{bands}×2 + {offset}），可能没拷完或与头文件不配套"
        )
    result.flight.update(
        samples=samples,
        lines=lines,
        bands=bands,
        data_type=dtype,
        interleave=interleave,
        spe_bytes=actual,
        wavelength_min=min(wavelengths) if wavelengths else None,
        wavelength_max=max(wavelengths) if wavelengths else None,
    )


def _check_gps(path: Path, result: FlightCheck) -> None:
    try:
        text = _read_text(path, _GPS_MAX_BYTES)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        result.errors.append(f"{GPS_NAME} 无法读取：{exc}")
        return
    rows = [r for r in text.splitlines() if r.strip()]
    if not rows:
        result.errors.append(f"{GPS_NAME} 是空文件")
        return
    header = [c.strip() for c in rows[0].split(",")]
    missing = [c for c in GPS_REQUIRED_COLUMNS if c not in header]
    if missing:
        first = rows[0][:80]
        result.errors.append(f"{GPS_NAME} 第一行不是定位表头（缺 {', '.join(missing)}），实际内容：{first}")
        return
    idx_time = header.index("UnixTime")
    records = rows[1:]
    if len(records) < 2:
        result.errors.append(f"{GPS_NAME} 只有 {len(records)} 条定位，至少需要 2 条")
        return
    try:
        t0 = int(records[0].split(",")[idx_time])
        t1 = int(records[-1].split(",")[idx_time])
    except (ValueError, IndexError):
        result.errors.append(f"{GPS_NAME} 的 UnixTime 列不是整数")
        return
    result.flight.update(gps_records=len(records), duration_s=max(0, t1 - t0))


def quick_check(input_dir: str | Path) -> FlightCheck:
    root = Path(input_dir)
    result = FlightCheck(input_dir=str(input_dir), name=root.name)
    if not root.is_dir():
        result.errors.append("架次目录不存在")
        return result
    hdr_path, spe_path, gps_path = root / HDR_NAME, root / SPE_NAME, root / GPS_NAME
    missing = [p.name for p in (hdr_path, spe_path, gps_path) if not p.is_file()]
    if missing:
        result.errors.append(f"缺少文件：{', '.join(missing)}")
        return result
    hdr = _check_header(hdr_path, result)
    if hdr is not None:
        _check_layout(hdr, spe_path, result)
    _check_gps(gps_path, result)
    return result


def find_flights(root: str | Path, *, depth: int = 2) -> list[Path]:
    """root 下含 figspec.hdr 的目录，最多往下找 depth 层。按名称排序。"""
    base = Path(root)
    if not base.is_dir():
        return []
    found: list[Path] = []
    level = [base]
    for _ in range(depth + 1):
        nxt: list[Path] = []
        for d in level:
            if (d / HDR_NAME).is_file():
                found.append(d)
                continue
            try:
                children = sorted(c for c in d.iterdir() if c.is_dir() and not c.name.startswith("."))
            except OSError:
                continue
            nxt.extend(children)
        level = nxt
    return sorted(found, key=lambda p: str(p))
