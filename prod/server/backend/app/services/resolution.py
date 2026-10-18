"""从航片头读取原始地面分辨率，供提交前选择拼图分辨率。

GSD = H / f_px（Kraus《Photogrammetry》）。f_px 用标定焦距按像宽缩放，
与 worker 的 geo.gsd_m 相同：1508.19 px @ 1440 宽。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from app.config import Settings

CALIB_WIDTH_PX = 1440.0
CALIB_FOCAL_PX = 1508.19
ORTHO_SCALES = (1, 2, 4)
_COLOR_RE = re.compile(r"^MAX_\d+_Color_[DWR]\.(jpg|jpeg)$", re.IGNORECASE)
_AGL_RE = re.compile(r"<Camera:AboveGroundAltitude>([^<]+)</Camera:AboveGroundAltitude>")
_READ = 1024 * 1024
_SAMPLE = 12


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


def measure_native_gsd(input_dir: Path, *, sample: int = _SAMPLE) -> dict:
    values: list[float] = []
    scanned = 0
    for path in input_dir.rglob("*"):
        if not path.is_file() or not _COLOR_RE.match(path.name):
            continue
        scanned += 1
        try:
            blob = path.read_bytes()[:_READ]
        except OSError:
            continue
        size = jpeg_size(blob)
        agl = _AGL_RE.search(blob.decode("latin1", errors="ignore"))
        if size is None or agl is None:
            continue
        try:
            height = float(agl.group(1))
        except ValueError:
            continue
        if height <= 1.0:
            continue
        values.append(gsd_m(height, size[0]))
        if len(values) >= sample:
            break
    if not values:
        raise HTTPException(400, "目录里没有带航高的 Color JPG，读不出原始分辨率")
    values.sort()
    native = values[len(values) // 2]
    return {
        "native_gsd_m": native,
        "n_samples": len(values),
        "n_color_seen": scanned,
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
