"""解析上传的 TIF/JPG：文件头、XMP、波段统计、预览与像元取值。"""

from __future__ import annotations

import io
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

MAX_BYTES = 512 * 1024 * 1024
FULL_LOAD_BYTES = 64 * 1024 * 1024
XMP_SCAN_BYTES = 8 * 1024 * 1024
TTL_S = 2 * 60 * 60
MAX_SESSIONS = 8
PREVIEW_MAX = 1600
ALLOWED = {".tif", ".tiff", ".jpg", ".jpeg"}

_XMP_BLOCK = re.compile(rb"<x:xmpmeta[\s\S]*?</x:xmpmeta>", re.IGNORECASE)
_ELEM = re.compile(
    r"<([A-Za-z_][\w-]*):([A-Za-z_][\w-]*)(?:\s[^>]*)?>([^<]*)</\1:\2>"
)
_ATTR = re.compile(
    r"\s([A-Za-z_][\w-]*):([A-Za-z_][\w-]*)=\"([^\"]*)\""
)
_SKIP_ELEM = {
    "xmpmeta",
    "RDF",
    "Description",
    "Seq",
    "Bag",
    "Alt",
    "li",
}

_FILE_WL = re.compile(r"(?:^|[_\-.])(\d{3,4})\s*nm(?:$|[_\-.])", re.IGNORECASE)
_FILE_COLOR = re.compile(r"(?:^|[_\-.])color(?:$|[_\-.])", re.IGNORECASE)
_FILE_DSM = re.compile(r"(?:^|[_\-.])dsm(?:$|[_\-.])", re.IGNORECASE)
_FILE_GROUP = re.compile(r"group\s*(\d+)", re.IGNORECASE)
_MS_GROUPS = ("450nm", "550nm", "650nm", "720nm", "750nm", "800nm", "850nm")
_RGB_CHANNELS = ("R", "G", "B", "Alpha")


@dataclass
class InspectSession:
    id: str
    path: Path
    filename: str
    created: float
    array: np.ndarray | None = None
    overview: np.ndarray | None = None
    lazy: bool = False
    io_lock: threading.Lock = field(default_factory=threading.Lock)
    segment_cache: dict[int, np.ndarray] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


_LOCK = threading.Lock()
_SESSIONS: dict[str, InspectSession] = {}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
            return None
        return value
    if isinstance(value, (bytes, bytearray)):
        if len(value) > 256:
            return f"<bytes {len(value)}>"
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, np.ndarray):
        if value.size > 32:
            return {"shape": list(value.shape), "dtype": str(value.dtype)}
        return [_jsonable(x) for x in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            return [_jsonable(x) for x in value[:64]] + [f"…共{len(value)}项"]
        return [_jsonable(x) for x in value]
    return str(value)


def _parse_xmp_tags(raw: str) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    if not raw:
        return tags
    for ns, name, value in _ELEM.findall(raw):
        text = value.strip()
        if not text or name in _SKIP_ELEM:
            continue
        key = (ns, name, text)
        if key in seen:
            continue
        seen.add(key)
        tags.append({"ns": ns, "name": name, "value": text})
    for ns, name, value in _ATTR.findall(raw):
        if name in {"parseType", "about", "xmlns"} or ns in {"xmlns", "xml"}:
            continue
        text = value.strip()
        if not text:
            continue
        key = (ns, name, text)
        if key in seen:
            continue
        seen.add(key)
        tags.append({"ns": ns, "name": name, "value": text})
    return tags


def extract_xmp(path: Path) -> tuple[str, list[dict[str, str]]]:
    suffix = path.suffix.lower()
    raw = ""
    if suffix in {".tif", ".tiff"}:
        import tifffile

        with tifffile.TiffFile(path) as tf:
            tag = tf.pages[0].tags.get(700)
            if tag is not None:
                val = tag.value
                if isinstance(val, (bytes, bytearray)):
                    raw = bytes(val).decode("utf-8", "replace")
                else:
                    raw = str(val)
    if not raw:
        size = path.stat().st_size
        with path.open("rb") as fh:
            data = fh.read(size if size <= XMP_SCAN_BYTES else XMP_SCAN_BYTES)
        m = _XMP_BLOCK.search(data)
        raw = m.group(0).decode("utf-8", "replace") if m else ""
    return raw, _parse_xmp_tags(raw)


def _xmp_value(tags: list[dict[str, str]], *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for tag in tags:
        if tag["name"].lower() in wanted and tag["value"].strip():
            return tag["value"].strip()
    return None


def _fmt_nm(value: str | float | int | None) -> tuple[str | None, float | None]:
    if value is None or value == "":
        return None, None
    text = str(value).strip()
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return text, None
    nm = float(m.group(1))
    label = f"{int(nm)}nm" if nm == int(nm) else f"{nm:g}nm"
    return label, nm


def resolve_band_labels(
    filename: str,
    count: int,
    xmp_tags: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """给每个通道起名：450nm / Color 的 R、G、B，而不是「波段 1」。"""
    xmp_tags = xmp_tags or []
    stem = Path(filename).name
    file_label: str | None = None
    file_nm: float | None = None

    wl_file = _FILE_WL.search(stem)
    if wl_file:
        file_label, file_nm = _fmt_nm(wl_file.group(1))
    elif _FILE_COLOR.search(stem):
        file_label = "Color"
    elif _FILE_DSM.search(stem) or stem.upper() in {"DSM.TIF", "DSM.TIFF"}:
        file_label = "DSM"
    else:
        grp = _FILE_GROUP.search(stem)
        if grp:
            gi = int(grp.group(1))
            if gi == 0:
                file_label = "Color"
            elif 1 <= gi <= len(_MS_GROUPS):
                file_label = _MS_GROUPS[gi - 1]
                file_nm = float(file_label.replace("nm", ""))

    xmp_band = _xmp_value(xmp_tags, "BandName", "Band")
    xmp_wl = _xmp_value(xmp_tags, "CentralWavelength", "Wavelength")
    xmp_fwhm = _xmp_value(xmp_tags, "WavelengthFWHM", "FWHM")
    _, fwhm_nm = _fmt_nm(xmp_fwhm)
    wl_label, wl_nm = _fmt_nm(xmp_wl)
    if xmp_band:
        btxt = xmp_band.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", btxt) or re.search(r"nm", btxt, re.I):
            xmp_label, xmp_nm = _fmt_nm(btxt)
        elif btxt.lower() in {"color", "rgb", "redgreenblue"}:
            xmp_label, xmp_nm = "Color", None
        else:
            xmp_label, xmp_nm = btxt, wl_nm
    else:
        xmp_label, xmp_nm = wl_label, wl_nm

    product = file_label or xmp_label
    wavelength = file_nm if file_nm is not None else (xmp_nm if xmp_nm is not None else wl_nm)
    if product is None and wavelength is not None:
        product = f"{int(wavelength)}nm" if wavelength == int(wavelength) else f"{wavelength:g}nm"

    rgb_like = count >= 3 and (
        file_label == "Color"
        or xmp_label == "Color"
        or product == "Color"
        or bool(_FILE_COLOR.search(stem))
    )
    if not rgb_like and count >= 3 and wavelength is None and product is None:
        rgb_like = True
    if rgb_like:
        return [
            {
                "name": _RGB_CHANNELS[i] if i < len(_RGB_CHANNELS) else f"波段 {i + 1}",
                "wavelength_nm": None,
                "fwhm_nm": None,
                "product": "Color",
            }
            for i in range(count)
        ]

    name = product or "波段 1"
    if count == 1:
        return [
            {
                "name": name,
                "wavelength_nm": wavelength,
                "fwhm_nm": fwhm_nm,
                "product": name,
            }
        ]
    return [
        {
            "name": f"{name}-{i + 1}",
            "wavelength_nm": None,
            "fwhm_nm": None,
            "product": name,
        }
        for i in range(count)
    ]


def _exif_map(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".tif", ".tiff"}:
        return {}
    from PIL import Image
    from PIL.ExifTags import GPSTAGS, TAGS

    out: dict[str, Any] = {}
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return {}
            for key, val in exif.items():
                name = TAGS.get(key, str(key))
                if name == "GPSInfo":
                    continue
                out[name] = _jsonable(val)
            try:
                gps = exif.get_ifd(0x8825)
            except Exception:
                gps = None
            if gps:
                gps_out = {}
                for key, val in gps.items():
                    gps_out[GPSTAGS.get(key, str(key))] = _jsonable(val)
                out["GPSInfo"] = gps_out
    except Exception:
        return out
    return out


def _as_hwc(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr)
    if a.ndim == 2:
        return a
    if a.ndim == 3:
        if a.shape[0] <= 16 and a.shape[0] < min(a.shape[1], a.shape[2]):
            return np.moveaxis(a, 0, -1)
        return a
    return a.reshape(a.shape[-2], a.shape[-1])


def _hwc_shape(arr: np.ndarray) -> tuple[int, int, int]:
    if arr.ndim == 2:
        return int(arr.shape[0]), int(arr.shape[1]), 1
    return int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[-1])


def _load_array(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        from PIL import Image

        with Image.open(path) as im:
            return np.asarray(im)
    import tifffile

    try:
        return np.asarray(tifffile.memmap(path))
    except Exception:
        return np.asarray(tifffile.imread(path))


def _finite(band: np.ndarray, nodata: float | None) -> np.ndarray:
    x = band.astype(np.float64, copy=False)
    m = np.isfinite(x)
    if nodata is not None:
        m &= x != float(nodata)
    if np.issubdtype(band.dtype, np.floating):
        m &= np.abs(x) < 1e30
    return m


def _band_stats(
    hwc: np.ndarray, nodata: float | None, labels: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    if hwc.ndim == 2:
        planes = [hwc]
    else:
        planes = [hwc[..., i] for i in range(hwc.shape[-1])]
    rows = []
    for i, plane in enumerate(planes):
        mask = _finite(plane, nodata)
        vals = plane[mask].astype(np.float64) if mask.any() else np.array([], dtype=np.float64)
        row = {
            "index": i + 1,
            "name": (labels[i]["name"] if labels and i < len(labels) else f"波段 {i + 1}"),
            "wavelength_nm": labels[i].get("wavelength_nm") if labels and i < len(labels) else None,
            "fwhm_nm": labels[i].get("fwhm_nm") if labels and i < len(labels) else None,
            "dtype": str(plane.dtype),
            "valid": int(vals.size),
            "min": float(vals.min()) if vals.size else None,
            "max": float(vals.max()) if vals.size else None,
            "mean": float(vals.mean()) if vals.size else None,
            "std": float(vals.std()) if vals.size else None,
            "p2": float(np.percentile(vals, 2)) if vals.size else None,
            "p50": float(np.percentile(vals, 50)) if vals.size else None,
            "p98": float(np.percentile(vals, 98)) if vals.size else None,
        }
        rows.append(row)
    return rows


def _preview_png(hwc: np.ndarray, nodata: float | None) -> tuple[bytes, int, int, float]:
    from PIL import Image

    if hwc.ndim == 2:
        planes = [hwc]
    else:
        planes = [hwc[..., i] for i in range(min(3, hwc.shape[-1]))]
    h, w = planes[0].shape
    scale = 1.0
    if max(h, w) > PREVIEW_MAX:
        scale = PREVIEW_MAX / float(max(h, w))
        nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
        ys = np.linspace(0, h - 1, nh).astype(np.int64)
        xs = np.linspace(0, w - 1, nw).astype(np.int64)
        planes = [p[np.ix_(ys, xs)] for p in planes]
        h, w = nh, nw

    def stretch(plane: np.ndarray) -> np.ndarray:
        mask = _finite(plane, nodata)
        if not mask.any():
            return np.zeros(plane.shape, np.uint8)
        vals = plane[mask].astype(np.float64)
        if np.issubdtype(plane.dtype, np.integer) and np.iinfo(plane.dtype).bits <= 8:
            out = np.zeros(plane.shape, np.uint8)
            out[mask] = np.clip(vals, 0, 255).astype(np.uint8)
            return out
        lo, hi = np.percentile(vals, [2, 98])
        if hi <= lo:
            lo, hi = float(vals.min()), float(vals.max()) or 1.0
        g = np.zeros(plane.shape, np.float64)
        g[mask] = (vals - lo) / max(hi - lo, 1e-9)
        return np.clip(g * 255.0, 0, 255).astype(np.uint8)

    chans = [stretch(p) for p in planes]
    if len(chans) == 1:
        img = Image.fromarray(chans[0], mode="L")
    else:
        while len(chans) < 3:
            chans.append(chans[-1])
        rgb = np.stack(chans[:3], axis=-1)
        img = Image.fromarray(rgb, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=6)
    return buf.getvalue(), w, h, scale


def _tiff_file_info(path: Path) -> dict[str, Any]:
    import tifffile

    info: dict[str, Any] = {}
    tags: list[dict[str, Any]] = []
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        info["pages"] = len(tf.pages)
        info["is_geotiff"] = bool(getattr(tf, "is_geotiff", False))
        info["byteorder"] = tf.byteorder
        info["shape"] = list(page.shape)
        info["dtype"] = str(page.dtype)
        info["compression"] = str(page.compression) if page.compression else None
        info["photometric"] = str(page.photometric) if page.photometric else None
        info["planarconfig"] = str(page.planarconfig) if page.planarconfig else None
        info["tiled"] = bool(page.is_tiled)
        info["tile"] = list(page.tile) if page.tile else None
        info["rowsperstrip"] = int(page.rowsperstrip) if page.rowsperstrip else None
        nodata = None
        for code, tag in page.tags.items():
            name = tag.name
            if code == 700 or name in {"XMP", "ImageDescription"} and isinstance(tag.value, (bytes, bytearray)) and len(tag.value) > 400:
                tags.append({"id": int(code), "name": name, "value": f"<省略 {len(tag.value)} 字节，见 XMP 区>"})
                continue
            if name == "GDAL_NODATA":
                try:
                    nodata = float(str(tag.value).strip())
                except (TypeError, ValueError):
                    nodata = None
            tags.append({"id": int(code), "name": name, "value": _jsonable(tag.value)})
        info["nodata"] = nodata
        if getattr(tf, "is_geotiff", False):
            try:
                info["geotiff"] = _jsonable(tf.geotiff_metadata)
            except Exception:
                info["geotiff"] = None
        scale = page.tags.get("ModelPixelScaleTag")
        tie = page.tags.get("ModelTiepointTag")
        tfw = None
        if scale is not None and tie is not None:
            sx, sy = float(scale.value[0]), float(scale.value[1])
            tie_v = list(tie.value)
            if len(tie_v) >= 6:
                tfw = {
                    "origin_x": float(tie_v[3]),
                    "origin_y": float(tie_v[4]),
                    "pixel_w": sx,
                    "pixel_h": -sy if sy > 0 else sy,
                }
        info["geotransform"] = tfw
    info["tags"] = tags
    return info


def _geo_xy(info: dict[str, Any], col: int, row: int) -> tuple[float, float] | None:
    tfw = info.get("geotransform")
    if not tfw:
        return None
    x = float(tfw["origin_x"]) + (col + 0.5) * float(tfw["pixel_w"])
    y = float(tfw["origin_y"]) + (row + 0.5) * float(tfw["pixel_h"])
    return x, y


def _segment_hwc(seg: np.ndarray | None, shp: tuple[int, int, int, int]) -> np.ndarray | None:
    if seg is None:
        return None
    depth, length, width, samples = (int(x) for x in shp)
    arr = np.asarray(seg).reshape(depth, length, width, samples)
    arr = arr[0]
    if samples == 1:
        return arr[..., 0]
    return arr


def _tiff_layout(page: Any) -> dict[str, int | bool]:
    height = int(page.imagelength)
    width = int(page.imagewidth)
    samples = int(page.samplesperpixel or 1)
    tiled = bool(page.is_tiled)
    if tiled and page.tile:
        tile = list(page.tile)
        th, tw = int(tile[-2]), int(tile[-1])
    else:
        th = int(page.rowsperstrip or height)
        tw = width
    th = max(th, 1)
    tw = max(tw, 1)
    return {
        "height": height,
        "width": width,
        "samples": samples,
        "tiled": tiled,
        "th": th,
        "tw": tw,
        "n_tx": (width + tw - 1) // tw,
        "n_ty": (height + th - 1) // th,
    }


def _segment_index(layout: dict[str, int | bool], row: int, col: int) -> int:
    th = int(layout["th"])
    tw = int(layout["tw"])
    n_tx = int(layout["n_tx"])
    return (row // th) * n_tx + (col // tw)


def _decode_segment(page: Any, fh: Any, index: int) -> np.ndarray | None:
    offsets = page.dataoffsets
    counts = page.databytecounts
    if index < 0 or index >= len(offsets):
        return None
    count = int(counts[index])
    if count <= 0:
        return None
    fh.seek(int(offsets[index]))
    blob = fh.read(count)
    seg, _pos, shp = page.decode(blob, index)
    return _segment_hwc(seg, shp)


def _cached_segment(sess: InspectSession, page: Any, fh: Any, index: int) -> np.ndarray | None:
    cached = sess.segment_cache.get(index)
    if cached is not None:
        return cached
    arr = _decode_segment(page, fh, index)
    if arr is None:
        return None
    if len(sess.segment_cache) >= 8:
        sess.segment_cache.pop(next(iter(sess.segment_cache)))
    sess.segment_cache[index] = arr
    return arr


def _load_tiff_overview(path: Path) -> tuple[np.ndarray, int, int, int, str]:
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        layout = _tiff_layout(page)
        height = int(layout["height"])
        width = int(layout["width"])
        samples = int(layout["samples"])
        dtype = str(page.dtype)
        scale = 1.0 if max(height, width) <= PREVIEW_MAX else PREVIEW_MAX / float(max(height, width))
        nh = max(1, int(round(height * scale)))
        nw = max(1, int(round(width * scale)))
        ys = np.linspace(0, height - 1, nh).astype(np.int64)
        xs = np.linspace(0, width - 1, nw).astype(np.int64)
        fh = tf.filehandle
        rows: list[np.ndarray] = []
        for r in ys:
            idx = _segment_index(layout, int(r), 0)
            seg = _decode_segment(page, fh, idx)
            if seg is None:
                if samples == 1:
                    rows.append(np.zeros(nw, dtype=np.dtype(page.dtype)))
                else:
                    rows.append(np.zeros((nw, samples), dtype=np.dtype(page.dtype)))
                continue
            local = int(r) % int(layout["th"])
            if seg.ndim == 2:
                line = seg[min(local, seg.shape[0] - 1)]
                rows.append(line[xs] if line.ndim == 1 else line[xs, ...])
            else:
                line = seg[min(local, seg.shape[0] - 1)]
                rows.append(line[xs] if line.ndim == 1 else line[xs, ...])
        overview = np.stack(rows, axis=0)
        return overview, height, width, samples, dtype


def _pixel_from_tiff(sess: InspectSession, col: int, row: int) -> list[Any]:
    import tifffile

    with sess.io_lock:
        with tifffile.TiffFile(sess.path) as tf:
            page = tf.pages[0]
            layout = _tiff_layout(page)
            idx = _segment_index(layout, row, col)
            seg = _cached_segment(sess, page, tf.filehandle, idx)
            if seg is None:
                raise ValueError("无法读取该像元")
            local_r = row % int(layout["th"])
            local_c = col % int(layout["tw"])
            local_r = min(local_r, seg.shape[0] - 1)
            if seg.ndim == 2:
                pix = seg[local_r, local_c]
                return [pix]
            pix = seg[local_r, local_c]
            if np.ndim(pix) == 0:
                return [pix]
            return [pix[i] for i in range(pix.shape[-1])]


def _should_lazy(height: int, width: int, count: int, dtype: np.dtype | str) -> bool:
    item = np.dtype(dtype).itemsize
    return int(height) * int(width) * int(count) * int(item) > FULL_LOAD_BYTES


def _purge_locked() -> None:
    now = time.time()
    dead = [sid for sid, s in _SESSIONS.items() if now - s.created > TTL_S]
    while len(_SESSIONS) - len(dead) >= MAX_SESSIONS and _SESSIONS:
        oldest = min(_SESSIONS.values(), key=lambda s: s.created)
        dead.append(oldest.id)
    for sid in dict.fromkeys(dead):
        sess = _SESSIONS.pop(sid, None)
        if sess is None:
            continue
        try:
            sess.path.unlink(missing_ok=True)
        except OSError:
            pass


def inspect_dir() -> Path:
    root = Path("/data/.inspect")
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        root = Path("/tmp/mosaic_inspect")
        root.mkdir(parents=True, exist_ok=True)
        return root


def create_session(filename: str, data: bytes) -> InspectSession:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("仅支持 TIF / TIFF / JPG / JPEG")
    if not data:
        raise ValueError("文件是空的")
    if len(data) > MAX_BYTES:
        raise ValueError(f"文件超过 {MAX_BYTES // (1024 * 1024)} MB")
    sid = uuid.uuid4().hex
    path = inspect_dir() / f"{sid}{suffix}"
    path.write_bytes(data)
    return create_session_from_path(filename, path)


def create_session_from_path(filename: str, path: Path) -> InspectSession:
    suffix = path.suffix.lower()
    if suffix not in ALLOWED:
        path.unlink(missing_ok=True)
        raise ValueError("仅支持 TIF / TIFF / JPG / JPEG")
    if not path.exists() or path.stat().st_size <= 0:
        path.unlink(missing_ok=True)
        raise ValueError("文件是空的")
    if path.stat().st_size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(f"文件超过 {MAX_BYTES // (1024 * 1024)} MB")
    sid = path.stem
    sess = InspectSession(id=sid, path=path, filename=filename, created=time.time())
    try:
        sess.meta = build_meta(sess)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    with _LOCK:
        _purge_locked()
        _SESSIONS[sid] = sess
    return sess


def get_session(sid: str) -> InspectSession | None:
    with _LOCK:
        _purge_locked()
        return _SESSIONS.get(sid)


def build_meta(sess: InspectSession) -> dict[str, Any]:
    path = sess.path
    suffix = path.suffix.lower()
    kind = "jpeg" if suffix in {".jpg", ".jpeg"} else "tiff"
    xmp_raw, xmp_tags = extract_xmp(path)
    nodata = None
    tiff_info: dict[str, Any] = {}
    stats_sampled = False

    if kind == "jpeg":
        arr = _as_hwc(_load_array(path))
        sess.array = arr
        sess.lazy = False
        h, w, count = _hwc_shape(arr)
        dtype = str(arr.dtype)
        source = arr
    else:
        tiff_info = _tiff_file_info(path)
        nodata = tiff_info.get("nodata")
        shape = list(tiff_info.get("shape") or [])
        dtype = str(tiff_info.get("dtype") or "uint8")
        if len(shape) >= 3:
            if shape[0] <= 16 and shape[0] < min(shape[1], shape[2]):
                count, h, w = int(shape[0]), int(shape[1]), int(shape[2])
            else:
                h, w, count = int(shape[0]), int(shape[1]), int(shape[2])
        elif len(shape) == 2:
            h, w, count = int(shape[0]), int(shape[1]), 1
        else:
            raise ValueError("无法读取影像尺寸")
        if _should_lazy(h, w, count, dtype):
            overview, oh, ow, oc, odtype = _load_tiff_overview(path)
            sess.overview = overview
            sess.array = None
            sess.lazy = True
            h, w, count = oh, ow, oc
            dtype = odtype
            source = overview
            stats_sampled = True
        else:
            arr = _as_hwc(_load_array(path))
            sess.array = arr
            sess.lazy = False
            h, w, count = _hwc_shape(arr)
            dtype = str(arr.dtype)
            source = arr

    labels = resolve_band_labels(sess.filename, count, xmp_tags)
    bands = _band_stats(source, nodata, labels)
    preview_bytes, pw, ph, pscale = _preview_png(source, nodata)
    if sess.lazy and max(h, w) > 0:
        pscale = ph / float(h)
    geo = tiff_info.get("geotransform")
    return {
        "preview_png": preview_bytes,
        "id": sess.id,
        "filename": sess.filename,
        "format": kind,
        "size_bytes": path.stat().st_size,
        "width": int(w),
        "height": int(h),
        "count": count,
        "dtype": dtype,
        "nodata": nodata,
        "stats_sampled": stats_sampled,
        "bands": bands,
        "xmp": {"raw": xmp_raw, "tags": xmp_tags},
        "exif": _exif_map(path),
        "tiff": {k: v for k, v in tiff_info.items() if k != "nodata"} if tiff_info else None,
        "geotransform": geo,
        "preview": {"width": pw, "height": ph, "scale": pscale},
    }


def public_meta(sess: InspectSession) -> dict[str, Any]:
    meta = dict(sess.meta)
    meta.pop("preview_png", None)
    return meta


def preview_png(sess: InspectSession) -> bytes:
    png = sess.meta.get("preview_png")
    if isinstance(png, (bytes, bytearray)):
        return bytes(png)
    source = sess.overview if sess.lazy and sess.overview is not None else sess.array
    assert source is not None
    data, _, _, _ = _preview_png(source, sess.meta.get("nodata"))
    sess.meta["preview_png"] = data
    return data


def sample_pixel(sess: InspectSession, col: int, row: int) -> dict[str, Any]:
    h = int(sess.meta.get("height") or 0)
    w = int(sess.meta.get("width") or 0)
    if h <= 0 or w <= 0:
        if sess.array is not None:
            h, w = sess.array.shape[:2]
        elif sess.overview is not None:
            h, w = sess.overview.shape[:2]
    if col < 0 or row < 0 or col >= w or row >= h:
        raise ValueError("像元超出影像范围")
    if sess.lazy:
        raw = _pixel_from_tiff(sess, col, row)
    else:
        arr = sess.array
        assert arr is not None
        if arr.ndim == 2:
            raw = [arr[row, col]]
        else:
            raw = [arr[row, col, i] for i in range(arr.shape[-1])]
    values = []
    for v in raw:
        if isinstance(v, (np.floating, float)):
            fv = float(v)
            values.append(None if not np.isfinite(fv) else fv)
        else:
            values.append(_jsonable(v))
    rgb = None
    hx = None
    if len(values) >= 3 and all(isinstance(v, (int, float)) for v in values[:3]):
        nums = [float(v) for v in values[:3]]
        if max(nums) <= 255 and min(nums) >= 0:
            rgb = [int(round(v)) for v in nums]
            hx = "".join(f"{c:02X}" for c in rgb)
    xy = _geo_xy(sess.meta, col, row)
    meta_bands = list(sess.meta.get("bands") or [])
    channels = []
    for i, value in enumerate(values):
        info = meta_bands[i] if i < len(meta_bands) else {}
        channels.append(
            {
                "name": info.get("name") or f"波段 {i + 1}",
                "value": value,
                "wavelength_nm": info.get("wavelength_nm"),
            }
        )
    return {
        "col": col,
        "row": row,
        "values": values,
        "bands": channels,
        "rgb": rgb,
        "hex": hx,
        "x": xy[0] if xy else None,
        "y": xy[1] if xy else None,
    }
