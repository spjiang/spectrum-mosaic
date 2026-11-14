"""从航片头读取原始地面分辨率和测区规模，供提交前选择拼图分辨率、估算并发。

GSD = H / f_px（Kraus《Photogrammetry》）。f_px 用标定焦距按像宽缩放，
与 worker 的 geo.gsd_m 相同：1508.19 px @ 1440 宽。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from fastapi import HTTPException

from app.config import Settings

CALIB_WIDTH_PX = 1440.0
CALIB_FOCAL_PX = 1508.19
ORTHO_SCALES = (1, 2, 4)
_NAME_RE = re.compile(
    r"^MAX_(\d+)_(Color|(?P<wl>\d+)nm)_(?P<role>[DWRdw])\.(?P<ext>jpg|jpeg|tif|tiff)$",
    re.IGNORECASE,
)
_AGL_RE = re.compile(r"<Camera:AboveGroundAltitude>([^<]+)</Camera:AboveGroundAltitude>")
_LAT_RE = re.compile(r"<Camera:PositionLatitudeFused>([^<]+)</Camera:PositionLatitudeFused>")
_LON_RE = re.compile(r"<Camera:PositionLongitudeFused>([^<]+)</Camera:PositionLongitudeFused>")
_READ = 1024 * 1024
_SAMPLE = 12
# 航线很长时不必每张都读文件头。按文件名排序后均匀取样，并保留最后一张，包围盒才不会缺两头。
_POS_CAP = 80


def gsd_m(agl_m: float, width: int) -> float:
    focal = CALIB_FOCAL_PX * (float(width) / CALIB_WIDTH_PX)
    return float(agl_m) / max(focal, 1e-6)


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """从 JPEG SOF 读宽高。只看文件头，不解码像素。"""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 8 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            if width > 0 and height > 0:
                return width, height
            return None
        if marker == 0xD8 or marker == 0xD9 or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            return None
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if length < 2:
            return None
        i += 2 + length
    return None


def _under_roots(path: Path, settings: Settings) -> bool:
    roots = [Path(r).expanduser().resolve() for r in settings.data_root_list()]
    if not roots:
        return str(path).startswith("/data")
    return any(path == root or root in path.parents for root in roots)


def resolve_input_dir(raw: str, settings: Settings) -> Path:
    text = (raw or "").strip()
    if not text.startswith("/data"):
        raise HTTPException(400, "输入目录须位于容器 /data 下")
    path = Path(text).expanduser().resolve()
    if not _under_roots(path, settings):
        raise HTTPException(400, "输入目录不在允许的数据根下")
    if not path.is_dir():
        raise HTTPException(400, "输入目录不存在")
    return path


def _float_tag(pattern: re.Pattern[str], text: str) -> float | None:
    found = pattern.search(text)
    if found is None:
        return None
    try:
        return float(found.group(1))
    except ValueError:
        return None


def _spread(paths: list[Path], cap: int) -> list[Path]:
    ordered = sorted(paths, key=lambda path: path.name)
    if len(ordered) <= cap:
        return ordered
    step = max(1, len(ordered) // cap)
    picked = ordered[::step]
    if ordered[-1] not in picked:
        picked.append(ordered[-1])
    return picked


def _span_m(lats: list[float], lons: list[float], width: int, height: int, gsd: float) -> tuple[float, float] | None:
    """相机位置包围盒再加一张像片的地面幅宽。规划用，不是交付格网。"""
    if not lats or not lons or gsd <= 0:
        return None
    lat0, lat1 = min(lats), max(lats)
    lon0, lon1 = min(lons), max(lons)
    mid = (lat0 + lat1) / 2.0
    north_m = abs(lat1 - lat0) * 111_320.0
    east_m = abs(lon1 - lon0) * 111_320.0 * math.cos(math.radians(mid))
    return east_m + width * gsd, north_m + height * gsd


def measure_native_gsd(input_dir: Path, *, sample: int = _SAMPLE) -> dict:
    """读测区文件名和 Color 文件头。

    返回原始分辨率，以及像幅、波段数、航线地面范围。范围用来估算正射像元，
    不作为交付格网。
    """
    colors: list[Path] = []
    ms_bands: set[str] = set()
    for path in input_dir.rglob("*"):
        if not path.is_file():
            continue
        matched = _NAME_RE.match(path.name)
        # 原始 D 和辐射校正 R 都是航片。W 是白板，不进测区统计。
        if matched is None or matched.group("role").upper() == "W":
            continue
        if matched.group(2).lower() == "color" and matched.group("ext").lower() in {"jpg", "jpeg"}:
            colors.append(path)
        elif matched.group("wl"):
            ms_bands.add(f"{int(matched.group('wl'))}nm")

    values: list[float] = []
    width = height = None
    lats: list[float] = []
    lons: list[float] = []
    for path in _spread(colors, _POS_CAP):
        try:
            blob = path.read_bytes()[:_READ]
        except OSError:
            continue
        text = blob.decode("latin1", errors="ignore")
        size = jpeg_size(blob)
        if size is not None and width is None:
            width, height = size
        agl = _float_tag(_AGL_RE, text)
        if size is not None and agl is not None and agl > 1.0 and len(values) < sample:
            values.append(gsd_m(agl, size[0]))
        lat = _float_tag(_LAT_RE, text)
        lon = _float_tag(_LON_RE, text)
        if lat is not None and lon is not None:
            lats.append(lat)
            lons.append(lon)
    if not values or width is None or height is None:
        raise HTTPException(400, "目录里没有带航高的 Color JPG，读不出原始分辨率")
    values.sort()
    native = values[len(values) // 2]
    span = _span_m(lats, lons, width, height, native)
    bands = ["Color", *sorted(ms_bands, key=lambda name: int(name[:-2]))]
    return {
        "native_gsd_m": native,
        "n_samples": len(values),
        "n_color_seen": len(colors),
        "n_color": len(colors),
        "width": width,
        "height": height,
        "bands": bands,
        "n_ms_bands": len(ms_bands),
        "span_x_m": None if span is None else span[0],
        "span_y_m": None if span is None else span[1],
        "scales": list(ORTHO_SCALES),
        "choices": [
            {
                "scale": scale,
                "ortho_gsd_m": native * scale,
                "dsm_gsd_m": native * scale * 2.0,
            }
            for scale in ORTHO_SCALES
        ],
    }


def needs_resolution_choice(params: dict, snap: dict) -> bool:
    """提交时要不要按航片写成 DSM/正射 GSD。

    锁定交付格网时不改。模版已经写好 dsm_gsd 时也不改，避免覆盖人工分辨率。
    模版只有倍数、没有 dsm_gsd 时必须写：否则 worker 会在空三之后用自标定焦距
    重估，格网随 f 漂移（S810 任务 #5 正射从 1221 块变成 1517 块就是这个原因）。
    """
    if snap.get("grid_reference"):
        return False
    if params.get("ortho_scale") not in (None, ""):
        return True
    if snap.get("dsm_gsd") not in (None, ""):
        return False
    return snap.get("ortho_scale") not in (None, "")


def apply_resolution_choice(snap: dict, input_dir: str, settings: Settings) -> None:
    """用户选了拼图倍数时，用实测原始分辨率写入 DSM/正射 GSD。

    锁定交付格网时不改，避免下拉把验收格网换掉。
    """
    if snap.get("grid_reference"):
        return
    raw = snap.get("ortho_scale")
    if raw is None or raw == "":
        return
    try:
        scale = int(raw)
    except (TypeError, ValueError):
        return
    if scale not in ORTHO_SCALES:
        raise HTTPException(400, "拼图分辨率只能选 1、2、4 倍原始分辨率")
    info = measure_native_gsd(resolve_input_dir(input_dir, settings))
    native = float(info["native_gsd_m"])
    ortho = native * scale
    snap["ortho_scale"] = scale
    snap["native_gsd_m"] = native
    snap["ortho_gsd_m"] = ortho
    snap["dsm_gsd"] = ortho * 2.0
