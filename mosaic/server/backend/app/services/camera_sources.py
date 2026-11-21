"""相机类型：提交前识别输入目录是哪种相机，并给出像素焦距。

后端与 worker 在不同镜像里，这里的取值和型号表要与 worker ms_mosaic/sources.py 保持一致。
"""

from __future__ import annotations

import re
from pathlib import Path

AUTO = "auto"
MAX_MS = "max_ms"
WAYHO_RGB = "wayho_rgb"
CAMERA_SOURCES = (AUTO, MAX_MS, WAYHO_RGB)
SOURCE_LABELS = {
    MAX_MS: "MAX 多光谱相机",
    WAYHO_RGB: "Wayho VEN 系列 RGB 相机",
}

MAX_NAME_RE = re.compile(
    r"^MAX_(\d+)_(Color|(?P<wl>\d+)nm)_(?P<role>[DWRdw])\.(?P<ext>jpg|jpeg|tif|tiff)$",
    re.IGNORECASE,
)
SEQ_NAME_RE = re.compile(r"^(\d+)\.(tif|tiff|jpg|jpeg)$", re.IGNORECASE)
RIG_RE = re.compile(r"<Camera:RigName>([^<]+)</Camera:RigName>")
FOCAL_MM_RE = re.compile(r"<Camera:PerspectiveFocalLength>([^<]+)</Camera:PerspectiveFocalLength>")

# 型号前缀 → 像元尺寸（微米），按前缀最长匹配。
WAYHO_PIXEL_UM: dict[str, float] = {
    "VEN-1220": 1.85,
}
_WAYHO_MAKE = "wayho"
_WAYHO_RIG_PREFIX = "VEN-"


def normalize_source(raw: object) -> str:
    text = str(raw or "").strip().lower() or AUTO
    if text not in CAMERA_SOURCES:
        raise ValueError(f"相机类型只能是 {' / '.join(CAMERA_SOURCES)}，收到 {raw!r}")
    return text


def tiff_head(path: Path) -> tuple[int, int, str, str]:
    """TIF 宽、高、XMP 文本、厂商。只读文件头，不解码像素。"""
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        xmp_tag = page.tags.get(700)
        make_tag = page.tags.get(271)
        raw = xmp_tag.value if xmp_tag is not None else b""
        xmp = bytes(raw).decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        make = str(make_tag.value).strip() if make_tag is not None else ""
        return int(page.imagewidth), int(page.imagelength), xmp, make


def rig_name(xmp: str) -> str:
    found = RIG_RE.search(xmp or "")
    return found.group(1).strip() if found else ""


def is_wayho(xmp: str, make: str) -> bool:
    return rig_name(xmp).upper().startswith(_WAYHO_RIG_PREFIX) or make.strip().lower() == _WAYHO_MAKE


def pixel_um_for(rig: str, override: float | None = None) -> float | None:
    if override is not None and float(override) > 0:
        return float(override)
    name = rig.upper()
    for prefix in sorted(WAYHO_PIXEL_UM, key=len, reverse=True):
        if name.startswith(prefix):
            return WAYHO_PIXEL_UM[prefix]
    return None


def focal_px_for(xmp: str, override_pixel_um: float | None = None) -> float | None:
    """XMP 焦距（mm）÷ 像元尺寸 → 像素焦距。缺任一项返回 None。"""
    found = FOCAL_MM_RE.search(xmp or "")
    if not found:
        return None
    try:
        focal_mm = float(found.group(1))
    except ValueError:
        return None
    pixel = pixel_um_for(rig_name(xmp), override_pixel_um)
    if focal_mm <= 0 or not pixel:
        return None
    return focal_mm * 1000.0 / pixel


def detect_source(files: list[Path]) -> str:
    """按文件名和首张序号影像的型号认相机。认不出时报错，说明支持的命名。"""
    if any(MAX_NAME_RE.match(p.name) for p in files):
        return MAX_MS
    numbered = sorted((p for p in files if SEQ_NAME_RE.match(p.name)), key=lambda p: p.name)
    if numbered:
        first = numbered[0]
        if first.suffix.lower() in {".tif", ".tiff"}:
            _w, _h, xmp, make = tiff_head(first)
            if is_wayho(xmp, make):
                return WAYHO_RGB
        raise ValueError(
            f"影像按序号命名（如 {first.name}），但读不出 Wayho 型号。"
            f"确认是 Wayho VEN 相机后，请在处理方案里把相机类型设为 {WAYHO_RGB}"
        )
    raise ValueError(
        "目录里没有可识别的航片。支持的命名：MAX 多光谱相机 MAX_<编号>_<Color|波长nm>_<D|W|R>.jpg/tif；"
        "Wayho VEN 系列 RGB 相机 <编号>.tif"
    )
