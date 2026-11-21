"""相机适配：把不同相机拍的航片目录读成同一套曝光（Shot）。

空三、DSM、正射只认 Shot：位置、姿态、相对航高、各波段文件和内参初值。姿态统一为
「pitch=-90 为正下视」。各相机在命名、姿态写法、焦距和高程上的差异都在这里处理，
后面的流程不分相机。

- MAX 多光谱相机：``MAX_<编号>_<Color|波长nm>_<D|W|R>.jpg/tif``，读法见 catalog。
- Wayho VEN 系列 RGB 相机：``<编号>.tif``，三通道 8 位，XMP 用 Pix4D Camera 命名空间。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from ms_mosaic.catalog import (
    NAME_RE,
    FileRecord,
    Pos,
    Shot,
    _xmp_blob,
    _xmp_matches,
    _xyz_from_exif_gps,
    group_shots,
    scan_directory,
)

AUTO = "auto"
MAX_MS = "max_ms"
WAYHO_RGB = "wayho_rgb"
CAMERA_SOURCES = (AUTO, MAX_MS, WAYHO_RGB)
SOURCE_LABELS = {
    MAX_MS: "MAX 多光谱相机",
    WAYHO_RGB: "Wayho VEN 系列 RGB 相机",
}

SEQ_NAME_RE = re.compile(r"^(\d+)\.(tif|tiff|jpg|jpeg)$", re.IGNORECASE)
_RIG_RE = re.compile(r"<Camera:RigName>([^<]+)</Camera:RigName>")

# 型号前缀 → 像元尺寸（微米）。按前缀最长匹配。
# VEN-1220 用 Sony IMX226，推荐输出 4024×3036，像元 1.85 µm。
WAYHO_PIXEL_UM: dict[str, float] = {
    "VEN-1220": 1.85,
}
_WAYHO_MAKE = "wayho"
_WAYHO_RIG_PREFIX = "VEN-"


@dataclass(frozen=True)
class SourceScan:
    """编目结果：用了哪种相机读法，读出的曝光，以及要写进日志的说明。"""

    source: str
    detected: bool
    shots: dict[int, Shot]
    notes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass(frozen=True)
class _Exif:
    make: str = ""
    focal_mm: float | None = None
    plane_px_per_mm: float | None = None


def normalize_source(raw: str | None) -> str:
    text = str(raw or "").strip().lower() or AUTO
    if text not in CAMERA_SOURCES:
        raise ValueError(f"相机类型只能是 {' / '.join(CAMERA_SOURCES)}，收到 {raw!r}")
    return text


def _as_float(value) -> float | None:
    """EXIF 有理数可能是 (分子, 分母)、IFDRational 或普通数。"""
    try:
        if isinstance(value, tuple) and len(value) == 2:
            num, den = float(value[0]), float(value[1])
            return num / den if den else None
        out = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return out if math.isfinite(out) else None


def _plane_px_per_mm(resolution, unit) -> float | None:
    res = _as_float(resolution)
    if res is None or res <= 0:
        return None
    per_unit_mm = {2: 25.4, 3: 10.0, 4: 1.0}.get(int(unit or 2))
    return res / per_unit_mm if per_unit_mm else None


def _read_exif(path: Path) -> _Exif:
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        import tifffile

        with tifffile.TiffFile(path) as tf:
            tags = tf.pages[0].tags
            make_tag = tags.get(271)
            exif_tag = tags.get(34665)
            make = str(make_tag.value).strip() if make_tag is not None else ""
            exif = exif_tag.value if exif_tag is not None and isinstance(exif_tag.value, dict) else {}
        return _Exif(
            make=make,
            focal_mm=_as_float(exif.get("FocalLength")),
            plane_px_per_mm=_plane_px_per_mm(
                exif.get("FocalPlaneXResolution"), exif.get("FocalPlaneResolutionUnit")
            ),
        )
    from PIL import Image

    with Image.open(path) as im:
        exif = im.getexif()
        make = str(exif.get(271) or "").strip()
        sub = exif.get_ifd(0x8769)
    return _Exif(
        make=make,
        focal_mm=_as_float(sub.get(37386)),
        plane_px_per_mm=_plane_px_per_mm(sub.get(41486), sub.get(41488)),
    )


def _rig_name(xmp: str) -> str:
    found = _RIG_RE.search(xmp) if xmp else None
    return found.group(1).strip() if found else ""


def _is_wayho(path: Path) -> bool:
    if _rig_name(_xmp_blob(path)).upper().startswith(_WAYHO_RIG_PREFIX):
        return True
    return _read_exif(path).make.lower() == _WAYHO_MAKE


def detect_source(root: Path) -> str:
    """按目录里的文件认相机。MAX 命名优先；序号命名再看 XMP 型号或 EXIF 厂商。"""
    files = sorted(p for p in Path(root).iterdir() if p.is_file())
    if any(NAME_RE.match(p.name) for p in files):
        return MAX_MS
    numbered = [p for p in files if SEQ_NAME_RE.match(p.name)]
    if numbered:
        if _is_wayho(numbered[0]):
            return WAYHO_RGB
        raise ValueError(
            f"{root} 里的影像按序号命名（如 {numbered[0].name}），但读不出 Wayho 型号。"
            f"确认是 Wayho VEN 相机后，请在处理方案里把相机类型设为 {WAYHO_RGB}"
        )
    raise ValueError(
        f"{root} 里没有可识别的航片。支持的命名："
        "MAX 多光谱相机 MAX_<编号>_<Color|波长nm>_<D|W|R>.jpg/tif；"
        "Wayho VEN 系列 RGB 相机 <编号>.tif"
    )


def wayho_pixel_um(rig: str, exif_px_per_mm: float | None, override: float | None) -> tuple[float | None, str]:
    """像元尺寸（微米）和出处。方案填写优先，其次型号表，最后 EXIF 焦平面分辨率。"""
    if override is not None and float(override) > 0:
        return float(override), "处理方案"
    name = rig.upper()
    for prefix in sorted(WAYHO_PIXEL_UM, key=len, reverse=True):
        if name.startswith(prefix):
            return WAYHO_PIXEL_UM[prefix], f"型号 {prefix}"
    if exif_px_per_mm:
        return 1000.0 / exif_px_per_mm, "EXIF 焦平面分辨率"
    return None, ""


def _xmp_float(got: dict, key: str) -> float | None:
    found = got.get(key)
    return _as_float(found.group(1)) if found else None


def read_wayho(path: Path, index: int, *, pixel_size_um: float | None = None) -> tuple[FileRecord, dict]:
    """读一张 Wayho 航片。返回编目记录，以及焦距、像元、高程来源等说明。"""
    xmp = _xmp_blob(path)
    got = _xmp_matches(xmp)
    rig = _rig_name(xmp)
    exif = _read_exif(path)

    focal_mm = _xmp_float(got, "focal") or exif.focal_mm
    pixel_um, pixel_from = wayho_pixel_um(rig, exif.plane_px_per_mm, pixel_size_um)
    focal_px = focal_mm * 1000.0 / pixel_um if focal_mm and pixel_um else None

    lat = _xmp_float(got, "lat")
    lon = _xmp_float(got, "lon")
    alt = _xmp_float(got, "alt")
    gps = _xyz_from_exif_gps(path) if lat is None or lon is None or not alt else None
    if gps is not None:
        if lat is None or lon is None:
            lat, lon = gps[0], gps[1]
        alt = alt or gps[2]

    agl = _xmp_float(got, "agl") or 0.0
    # 椭球高恰好为 0 的航片不存在，0 是没写高程的占位。
    relative = not alt
    pos = None
    if lat is not None and lon is not None:
        # Pix4D Camera 命名空间写的是机体姿态：pitch=roll=0 为水平飞行、镜头正下视。
        pos = Pos(
            lon=float(lon),
            lat=float(lat),
            alt_m=float(agl) if relative else float(alt),
            yaw_deg=_xmp_float(got, "yaw") or 0.0,
            pitch_deg=(_xmp_float(got, "pitch") or 0.0) - 90.0,
            roll_deg=_xmp_float(got, "roll") or 0.0,
            agl_m=float(agl),
            focal_mm=float(focal_mm) if focal_mm else 0.0,
            alt_relative=relative,
        )
    uuid = got["uuid"].group(1) if got.get("uuid") else ""
    record = FileRecord(
        path=path,
        index=index,
        band="Color",
        role="D",
        kind="rgb",
        pos=pos,
        capture_uuid=uuid,
        focal_px=focal_px,
        camera_model=rig or exif.make or "Wayho",
    )
    info = {
        "rig": rig,
        "focal_mm": focal_mm,
        "pixel_um": pixel_um,
        "pixel_from": pixel_from,
        "focal_px": focal_px,
        "relative": relative,
    }
    return record, info


def _wayho_notes(infos: list[dict], n_files: int) -> tuple[str, ...]:
    if not infos:
        return ()
    first = infos[0]
    notes: list[str] = []
    model = first["rig"] or "未知型号"
    if first["focal_px"]:
        notes.append(
            f"型号 {model}，焦距 {first['focal_mm']:g} mm，像元 {first['pixel_um']:g} µm"
            f"（{first['pixel_from']}），焦距初值 {first['focal_px']:.0f} px，空三自标定"
        )
    else:
        missing = "焦距" if not first["focal_mm"] else "像元尺寸"
        notes.append(
            f"型号 {model}：读不到{missing}，焦距初值按像宽估计，可能偏差较大。"
            "请在处理方案里填写像元尺寸（pixel_size_um）"
        )
    n_rel = sum(1 for info in infos if info["relative"])
    if n_rel:
        notes.append(
            f"{n_rel}/{n_files} 张没有绝对高程：相机高度取 XMP 相对航高，"
            "DSM 高程是相对起飞点的高度，不是椭球高"
        )
    return tuple(notes)


def _scan_wayho(
    root: Path, *, max_index: int | None, pixel_size_um: float | None
) -> tuple[dict[int, Shot], tuple[str, ...]]:
    records: list[FileRecord] = []
    infos: list[dict] = []
    for path in sorted(Path(root).iterdir()):
        matched = SEQ_NAME_RE.match(path.name)
        if matched is None or not path.is_file():
            continue
        index = int(matched.group(1))
        if max_index is not None and index > max_index:
            continue
        record, info = read_wayho(path, index, pixel_size_um=pixel_size_um)
        records.append(record)
        infos.append(info)
    return group_shots(records), _wayho_notes(infos, len(records))


def scan_input(
    root: Path,
    *,
    max_index: int | None = None,
    camera_source: str | None = AUTO,
    pixel_size_um: float | None = None,
) -> SourceScan:
    """按相机类型读目录。auto 时自动识别；读不到任何航片时直接报错，说明按哪种相机读的。"""
    root = Path(root)
    source = normalize_source(camera_source)
    detected = source == AUTO
    if detected:
        source = detect_source(root)
    if source == MAX_MS:
        shots = scan_directory(root, max_index=max_index)
        notes: tuple[str, ...] = ()
        if pixel_size_um:
            notes = ("MAX 相机按标定比例给焦距初值，处理方案里的像元尺寸不使用",)
    else:
        shots, notes = _scan_wayho(root, max_index=max_index, pixel_size_um=pixel_size_um)
    if not shots:
        raise ValueError(f"按「{SOURCE_LABELS[source]}」在 {root} 里没有读到航片，请检查目录或相机类型")
    return SourceScan(source=source, detected=detected, shots=shots, notes=notes)
