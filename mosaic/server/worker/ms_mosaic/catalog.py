from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# D=原始航片，W=白板，R=辐射校正产品。三种都入册；白板在 usable_shots 里再剔。
NAME_RE = re.compile(
    r"^MAX_(\d+)_(Color|(?P<wl>\d+)nm)_(?P<role>[DWRdw])\.(?P<ext>jpg|jpeg|tif|tiff)$",
    re.IGNORECASE,
)
XMP_RE = {
    "yaw": re.compile(r"<Camera:Yaw>([^<]+)</Camera:Yaw>"),
    "pitch": re.compile(r"<Camera:Pitch>([^<]+)</Camera:Pitch>"),
    "roll": re.compile(r"<Camera:Roll>([^<]+)</Camera:Roll>"),
    "agl": re.compile(r"<Camera:AboveGroundAltitude>([^<]+)</Camera:AboveGroundAltitude>"),
    "lat": re.compile(r"<Camera:PositionLatitudeFused>([^<]+)</Camera:PositionLatitudeFused>"),
    "lon": re.compile(r"<Camera:PositionLongitudeFused>([^<]+)</Camera:PositionLongitudeFused>"),
    "alt": re.compile(r"<Camera:PositionAltitudeFused>([^<]+)</Camera:PositionAltitudeFused>"),
    "uuid": re.compile(r"<Camera:CaptureUUID>([^<]+)</Camera:CaptureUUID>"),
    "focal": re.compile(r"<Camera:PerspectiveFocalLength>([^<]+)</Camera:PerspectiveFocalLength>"),
    "rtk": re.compile(r"<Camera:RtkUsage>([^<]+)</Camera:RtkUsage>"),
}


@dataclass
class Pos:
    lon: float
    lat: float
    alt_m: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    agl_m: float
    focal_mm: float = 5.0
    rtk: int = 0


@dataclass
class FileRecord:
    path: Path
    index: int
    band: str
    role: str
    kind: str
    wavelength_nm: int | None = None
    pos: Pos | None = None
    capture_uuid: str = ""


@dataclass
class Shot:
    index: int
    rgb: FileRecord | None = None
    ms: dict[str, FileRecord] = field(default_factory=dict)
    pos: Pos | None = None
    capture_uuid: str = ""
    role: str = "D"


def parse_filename(path: Path) -> FileRecord | None:
    m = NAME_RE.match(path.name)
    if not m:
        return None
    index = int(m.group(1))
    role = m.group("role").upper()
    wl = m.group("wl")
    if wl:
        return FileRecord(
            path=path,
            index=index,
            band=f"{int(wl)}nm",
            role=role,
            kind="ms",
            wavelength_nm=int(wl),
        )
    return FileRecord(
        path=path,
        index=index,
        band="Color",
        role=role,
        kind="rgb",
        wavelength_nm=None,
    )


def _dms_to_deg(values, ref: str) -> float:
    seq = list(values)
    if len(seq) == 6:
        d, m, s = seq[0] / seq[1], seq[2] / seq[3], seq[4] / seq[5]
    elif len(seq) == 3:
        d, m, s = (float(x) for x in seq)
    else:
        raise ValueError(f"unexpected GPS tuple {values!r}")
    deg = float(d) + float(m) / 60.0 + float(s) / 3600.0
    if str(ref).upper().startswith("S") or str(ref).upper().startswith("W"):
        deg = -deg
    return deg


def _xmp_blob(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff"}:
        import tifffile

        with tifffile.TiffFile(path) as tf:
            tag = tf.pages[0].tags.get(700)
            if tag is None:
                return ""
            raw = tag.value
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw).decode("utf-8", "replace")
            return str(raw)
    data = path.read_bytes()
    m = re.search(br"<x:xmpmeta[\s\S]*?</x:xmpmeta>", data)
    return m.group(0).decode("utf-8", "replace") if m else ""


def _xmp_matches(xmp: str) -> dict:
    return {k: rx.search(xmp) for k, rx in XMP_RE.items()} if xmp else {}


def _attitude_from_xmp(got: dict) -> dict[str, float | int]:
    """姿态、相对高、焦距、RTK 只来自 XMP。辐射 _R 仍保留这些字段。"""
    return {
        "yaw_deg": float(got["yaw"].group(1)) if got.get("yaw") else 0.0,
        "pitch_deg": float(got["pitch"].group(1)) if got.get("pitch") else -90.0,
        "roll_deg": float(got["roll"].group(1)) if got.get("roll") else 0.0,
        "agl_m": float(got["agl"].group(1)) if got.get("agl") else 0.0,
        "focal_mm": float(got["focal"].group(1)) if got.get("focal") else 5.0,
        "rtk": int(float(got["rtk"].group(1))) if got.get("rtk") else 0,
    }


def _xyz_from_xmp_fused(got: dict) -> tuple[float, float, float] | None:
    """优先用 XMP 融合位置。_R 通常没有这三项。"""
    if not got.get("lat") or not got.get("lon"):
        return None
    alt = float(got["alt"].group(1)) if got.get("alt") else 0.0
    return float(got["lat"].group(1)), float(got["lon"].group(1)), alt


def _xyz_from_exif_gps(path: Path) -> tuple[float, float, float] | None:
    """EXIF GPS 经纬高。_D 与 _R 都有，作融合位置缺失时的回退。"""
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS

        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            gps = exif.get_ifd(0x8825)
            if not gps:
                return None
            gpsn = {GPSTAGS.get(k, str(k)): gps[k] for k in gps}
        if "GPSLatitude" not in gpsn:
            return None
        lat = _dms_to_deg(gpsn["GPSLatitude"], gpsn.get("GPSLatitudeRef", "N"))
        lon = _dms_to_deg(gpsn["GPSLongitude"], gpsn.get("GPSLongitudeRef", "E"))
        alt = float(gpsn.get("GPSAltitude") or 0)
        return lat, lon, alt
    if suffix in {".tif", ".tiff"}:
        import tifffile

        with tifffile.TiffFile(path) as tf:
            gps = tf.pages[0].tags.get(34853)
            if gps is None:
                return None
            g = gps.value
        if "GPSLatitude" not in g:
            return None
        lat = _dms_to_deg(g["GPSLatitude"], g.get("GPSLatitudeRef", "N"))
        lon = _dms_to_deg(g["GPSLongitude"], g.get("GPSLongitudeRef", "E"))
        alt_raw = g.get("GPSAltitude", 0)
        if isinstance(alt_raw, tuple) and len(alt_raw) == 2:
            alt = float(alt_raw[0]) / float(alt_raw[1])
        else:
            alt = float(alt_raw or 0)
        return lat, lon, alt
    return None


def read_sidecar(path: Path) -> tuple[Pos | None, str]:
    """读外方位初值。

    位置：XMP ``Position*Fused`` 优先；没有则用 EXIF GPS（_D/_R 都有）。
    姿态 / 相对高 / 焦距 / RTK：始终来自 XMP，避免 GPS 回退时把航高清成 0。
    """
    xmp = _xmp_blob(path)
    got = _xmp_matches(xmp)
    uuid = got["uuid"].group(1) if got.get("uuid") else ""
    att = _attitude_from_xmp(got)
    xyz = _xyz_from_xmp_fused(got)
    if xyz is None:
        xyz = _xyz_from_exif_gps(path)
    if xyz is None:
        return None, uuid
    lat, lon, alt_m = xyz
    return (
        Pos(
            lon=lon,
            lat=lat,
            alt_m=alt_m,
            yaw_deg=float(att["yaw_deg"]),
            pitch_deg=float(att["pitch_deg"]),
            roll_deg=float(att["roll_deg"]),
            agl_m=float(att["agl_m"]),
            focal_mm=float(att["focal_mm"]),
            rtk=int(att["rtk"]),
        ),
        uuid,
    )


def group_shots(records: list[FileRecord | None]) -> dict[int, Shot]:
    shots: dict[int, Shot] = {}
    for rec in records:
        if rec is None:
            continue
        shot = shots.get(rec.index)
        if shot is None:
            shot = Shot(index=rec.index)
            shots[rec.index] = shot
        if rec.kind == "rgb":
            shot.rgb = rec
        else:
            shot.ms[rec.band] = rec
        if rec.capture_uuid and not shot.capture_uuid:
            shot.capture_uuid = rec.capture_uuid
        if rec.pos is not None and shot.pos is None:
            shot.pos = rec.pos
    for shot in shots.values():
        if shot.pos is None:
            src = shot.rgb or (next(iter(shot.ms.values())) if shot.ms else None)
            if src is not None:
                shot.pos = src.pos
                if src.capture_uuid:
                    shot.capture_uuid = src.capture_uuid
        ms_roles = {r.role for r in shot.ms.values()}
        # 只有全是白板才标 W；原始 D 与辐射 R 都当正常航片。
        if ms_roles and ms_roles <= {"W"}:
            shot.role = "W"
        elif ms_roles and ms_roles <= {"R"}:
            shot.role = "R"
        else:
            shot.role = "D"
    return shots


def scan_directory(root: Path, max_index: int | None = None) -> dict[int, Shot]:
    records: list[FileRecord] = []
    for path in sorted(root.iterdir()):
        rec = parse_filename(path)
        if rec is None:
            continue
        if max_index is not None and rec.index > max_index:
            continue
        pos, uuid = read_sidecar(path)
        rec.pos = pos
        rec.capture_uuid = uuid
        records.append(rec)
    return group_shots(records)
