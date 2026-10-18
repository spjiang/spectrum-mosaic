"""把彩色正射缩成预览，并把拼接线面投到预览像素上，供页面点选。"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from app.models import JobRun
from app.services.geodesy import utm_to_wgs84, utm_zone_from_epsg, wgs84_to_utm
from app.services.inspect_media import _load_tiff_overview, _preview_png

ORTHO_NAME = "Orthomosaic_pix_surf_group0.tif"
SEAM_NAME = "seamlines.geojson"
EDIT_NAME = "seam_edit.geojson"
DSM_NAME = "DSM.tif"
VERSIONS_DIR = "versions"
VERSION_INDEX = "index.json"
_VERSION_ID = re.compile(r"v\d{3}")
_ZONE_RE = re.compile(r"UTM zone\s+(\d+)\s*([NS])?", re.IGNORECASE)
# 预览缓存跟着被归档的正射走。人工圈定文件留给下一次正射使用，不挪走。
_ARCHIVE_EXTRAS = (SEAM_NAME, "seam_preview.png", "seam_preview.json")


def _products_dir(job: JobRun) -> Path:
    candidates = []
    if job.products_dir:
        candidates.append(Path(job.products_dir))
    out = Path(job.output_dir)
    candidates.extend([out / "拼图结果", out])
    for path in candidates:
        if (path / ORTHO_NAME).is_file():
            return path
    versions = _read_index(Path(job.output_dir) / "附件" / VERSIONS_DIR) if getattr(job, "output_dir", None) else []
    if versions:
        raise HTTPException(400, "当前成果正在重算，上一版已保留在版本记录里")
    raise HTTPException(400, "还没有彩色正射，无法在图上显示拼接线")


def _extras_dir(job: JobRun) -> Path:
    out = getattr(job, "output_dir", None)
    if out and (Path(out) / "附件").is_dir():
        return Path(out) / "附件"
    products_dir = getattr(job, "products_dir", None)
    if products_dir and (Path(products_dir).parent / "附件").is_dir():
        return Path(products_dir).parent / "附件"
    try:
        products = _products_dir(job)
    except HTTPException:
        if out:
            return Path(out) / "附件"
        raise
    sibling = products.parent / "附件"
    if (sibling / SEAM_NAME).is_file() or sibling.is_dir():
        return sibling
    return products / "附件"


def _versions_root(job: JobRun) -> Path:
    return _extras_dir(job) / VERSIONS_DIR


def _read_index(root: Path) -> list[dict]:
    path = root / VERSION_INDEX
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("versions") or [])


def _write_index(root: Path, versions: list[dict]) -> None:
    path = root / VERSION_INDEX
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"versions": versions}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _version_dir(job: JobRun, version: str) -> Path:
    if not _VERSION_ID.fullmatch(version or ""):
        raise HTTPException(400, "版本号无效")
    path = _versions_root(job) / version
    if not (path / ORTHO_NAME).is_file():
        raise HTTPException(404, "没有这一版拼图成果")
    return path


def list_product_versions(job: JobRun) -> dict:
    versions = _read_index(_versions_root(job))
    current = None
    try:
        products = _products_dir(job)
    except HTTPException:
        products = None
    if products is not None:
        ortho = products / ORTHO_NAME
        current = {
            "id": "current",
            "label": "当前成果",
            "created_at": datetime.fromtimestamp(ortho.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "note": "正在使用的拼图结果",
        }
    return {"current": current, "versions": versions}


_PLACE_GRID = (
    ("左上", "上方", "右上"),
    ("左侧", "中部", "右侧"),
    ("左下", "下方", "右下"),
)


def describe_edit(job: JobRun, regions: list | None, clear_edits: bool = False) -> dict:
    """这次重算改了图上哪些位置、换成哪张照片。写入修改记录，不参与正射计算。"""
    if clear_edits:
        return {"summary": "整幅改回自动拼接线", "places": []}
    preview_path = _extras_dir(job) / "seam_preview.json"
    width, height = 1.0, 1.0
    if preview_path.is_file():
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
        width = float(preview.get("width") or 1.0)
        height = float(preview.get("height") or 1.0)
    try:
        catalog = _image_catalog(job)
    except HTTPException:
        catalog = {}
    places = []
    for region in regions or []:
        points = region.get("points") or []
        if len(points) < 3:
            continue
        cx = sum(float(p[0]) for p in points) / len(points)
        cy = sum(float(p[1]) for p in points) / len(points)
        col = 0 if cx < width / 3.0 else 1 if cx < width * 2.0 / 3.0 else 2
        row = 0 if cy < height / 3.0 else 1 if cy < height * 2.0 / 3.0 else 2
        image_id = int(region.get("image_id", -1))
        raw = catalog.get(image_id, "")
        stem = Path(raw).stem if raw else ""
        name = stem.replace("_Color_D", "").replace("MAX_", "MAX ") if stem else f"影像 {image_id}"
        places.append({"where": _PLACE_GRID[row][col], "name": name, "image_id": image_id})
    summary = "；".join(f"{item['where']}换成 {item['name']}" for item in places) or "没有有效范围"
    return {"summary": summary, "places": places}


def archive_current_products(job: JobRun, change: dict | None = None, label: str | None = None) -> dict | None:
    """重算正射前把现有成果改名留档。同一磁盘上是改名，不复制整幅影像。"""
    try:
        products = _products_dir(job)
    except HTTPException:
        return None
    extras = _extras_dir(job)
    extras.mkdir(parents=True, exist_ok=True)
    root = extras / VERSIONS_DIR
    root.mkdir(parents=True, exist_ok=True)
    versions = _read_index(root)
    version_id = f"v{len(versions) + 1:03d}"
    dest = root / version_id
    dest.mkdir()
    moved: list[str] = []
    for item in list(products.iterdir()):
        if not item.is_file():
            continue
        shutil.move(str(item), dest / item.name)
        moved.append(item.name)
    for name in _ARCHIVE_EXTRAS:
        src = extras / name
        if src.is_file():
            shutil.move(str(src), dest / name)
            moved.append(name)
    if not moved:
        dest.rmdir()
        return None
    record = {
        "id": version_id,
        "label": label or ("原始成果" if len(versions) == 0 else f"第 {len(versions)} 次调整"),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "重算拼接线之前的成果，文件仍在，可和当前成果对比",
        "files": moved,
        "dsm": str(dest / DSM_NAME) if (dest / DSM_NAME).is_file() else "",
        "products_dir": str(products),
        "change": change or {"summary": "", "places": []},
    }
    versions.append(record)
    _write_index(root, versions)
    return record


def _copy_file(src: Path, dest: Path) -> None:
    """复制出新文件。只读源文件，不移动、不硬链接。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.copying")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def _live_product_files(job: JobRun) -> list[Path]:
    products = _products_dir(job)
    extras = _extras_dir(job)
    files = [item for item in products.iterdir() if item.is_file()]
    for name in _ARCHIVE_EXTRAS:
        src = extras / name
        if src.is_file():
            files.append(src)
    return files


def _snapshot_copy(job: JobRun, files: list[Path], *, label: str, summary: str, base_id: str) -> dict:
    if not files:
        raise HTTPException(400, "没有可复制的成果文件")
    extras = _extras_dir(job)
    root = extras / VERSIONS_DIR
    root.mkdir(parents=True, exist_ok=True)
    versions = _read_index(root)
    version_id = f"v{len(versions) + 1:03d}"
    dest = root / version_id
    dest.mkdir()
    names: list[str] = []
    for src in files:
        _copy_file(src, dest / src.name)
        names.append(src.name)
    products = Path(str(getattr(job, "products_dir", "") or ""))
    if not products.is_dir():
        out = getattr(job, "output_dir", None)
        products = Path(out) / "拼图结果" if out else dest
    record = {
        "id": version_id,
        "label": label,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "从所选成果复制的新记录，源文件没有改动",
        "files": names,
        "dsm": str(dest / DSM_NAME) if (dest / DSM_NAME).is_file() else "",
        "products_dir": str(products),
        "base_id": base_id,
        "change": {"summary": summary, "places": []},
    }
    versions.append(record)
    _write_index(root, versions)
    return record


def start_seam_copy(job: JobRun, version_id: str) -> dict:
    """按选定的成果复制一份新的修改记录。源目录只读，原文件一个字节都不改。"""
    if version_id in {"", "current"}:
        record = _snapshot_copy(
            job,
            _live_product_files(job),
            label="基于当前成果",
            summary="基于当前成果复制了一份，准备拼接修改",
            base_id="current",
        )
        clear_seam_edit(job)
        return record
    if not _VERSION_ID.fullmatch(version_id or ""):
        raise HTTPException(400, "版本号无效")
    versions = _read_index(_versions_root(job))
    chosen = next((row for row in versions if row.get("id") == version_id), None)
    if chosen is None:
        raise HTTPException(404, "没有这一版修改记录")
    src = _version_dir(job, version_id)
    base_label = str(chosen.get("label") or version_id)
    try:
        live = _live_product_files(job)
    except HTTPException:
        live = []
    if live:
        _snapshot_copy(
            job,
            live,
            label="替换前的当前成果",
            summary=f"改以{base_label}为底之前，把当时的当前成果复制留档",
            base_id="current",
        )
    products = Path(str(chosen.get("products_dir") or ""))
    if not products.is_dir():
        out = getattr(job, "output_dir", None)
        products = Path(out) / "拼图结果" if out else src.parent
    products.mkdir(parents=True, exist_ok=True)
    extras = _extras_dir(job)
    extras.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in src.iterdir():
        if not item.is_file():
            continue
        target = (extras if item.name in _ARCHIVE_EXTRAS else products) / item.name
        _copy_file(item, target)
        copied += 1
    if copied == 0:
        raise HTTPException(400, "这一版没有可复制的成果文件")
    record = _snapshot_copy(
        job,
        [item for item in src.iterdir() if item.is_file()],
        label=f"基于{base_label}",
        summary=f"基于「{base_label}」复制了一份，准备拼接修改",
        base_id=version_id,
    )
    clear_seam_edit(job)
    return record


def restore_archived_products(job: JobRun, record: dict | None) -> None:
    """队列没投递出去时，把刚留档的成果移回拼图结果，避免当前目录被掏空。"""
    if not record:
        return
    products = Path(record.get("products_dir") or "")
    if not products.is_dir():
        return
    extras = _extras_dir(job)
    dest = extras / VERSIONS_DIR / str(record.get("id") or "")
    if not dest.is_dir():
        return
    products.mkdir(parents=True, exist_ok=True)
    for name in record.get("files") or []:
        src = dest / name
        if not src.is_file():
            continue
        target = extras if name in _ARCHIVE_EXTRAS else products
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), target / name)
    root = extras / VERSIONS_DIR
    _write_index(root, [row for row in _read_index(root) if row.get("id") != record.get("id")])
    if dest.is_dir() and not any(dest.iterdir()):
        dest.rmdir()


def _georef(path: Path) -> tuple[float, float, float, int, int, str]:
    import tifffile

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        scale = page.tags[33550].value
        tie = page.tags[33922].value
        height = int(page.imagelength)
        width = int(page.imagewidth)
        citation = ""
        ascii_tag = page.tags.get(34737)
        if ascii_tag is not None:
            citation = str(ascii_tag.value)
    gsd = float(scale[0])
    origin_x = float(tie[3])
    origin_y = float(tie[4])
    return origin_x, origin_y, gsd, height, width, citation


def _zone(citation: str) -> tuple[int, bool]:
    match = _ZONE_RE.search(citation or "")
    if not match:
        raise HTTPException(400, "正射影像没有 UTM 带号，拼接线对不到图上")
    zone = int(match.group(1))
    south = (match.group(2) or "N").upper() == "S"
    return zone, south


def _rings(geometry: dict) -> list[list]:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords[0]] if coords else []
    if kind == "MultiPolygon":
        return [poly[0] for poly in coords if poly]
    return []


def _simplify(points: np.ndarray, tol: float) -> np.ndarray:
    if len(points) <= 4:
        return points
    keep = [0]
    last = points[0]
    for i in range(1, len(points) - 1):
        if float(np.hypot(*(points[i] - last))) >= tol:
            keep.append(i)
            last = points[i]
    keep.append(len(points) - 1)
    return points[keep]


def build_seam_map(job: JobRun, version: str | None = None) -> dict:
    if version and version != "current":
        products = _version_dir(job, version)
        extras = products
    else:
        products = _products_dir(job)
        extras = _extras_dir(job)
    ortho = products / ORTHO_NAME
    seams = extras / SEAM_NAME
    if not seams.is_file():
        raise HTTPException(400, "还没有拼接线文件，正射完成后才能在图上调整")
    origin_x, origin_y, gsd, full_h, full_w, citation = _georef(ortho)
    zone, south = _zone(citation)
    preview_path = extras / "seam_preview.png"
    meta_path = extras / "seam_preview.json"
    stamp = f"{ortho.stat().st_mtime_ns}:{seams.stat().st_mtime_ns}:{full_w}x{full_h}"
    if preview_path.is_file() and meta_path.is_file():
        cached = json.loads(meta_path.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            cached["preview_ready"] = True
            return cached

    overview, _h, _w, _samples, _dtype = _load_tiff_overview(ortho)
    if overview.ndim == 3 and overview.shape[-1] >= 4:
        rgb = np.array(overview[..., :3])
        empty = overview[..., 3] == 0
        rgb[empty] = 236
        overview = rgb
    png, width, height, _scale = _preview_png(overview, None)
    preview_path.write_bytes(png)
    data = json.loads(seams.read_text(encoding="utf-8"))
    sx = width / float(full_w)
    sy = height / float(full_h)
    features = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        image_id = int(props.get("image_id", -1))
        if image_id < 0:
            continue
        for ring in _rings(feature.get("geometry") or {}):
            arr = np.asarray(ring, float)
            if arr.ndim != 2 or arr.shape[0] < 4 or arr.shape[1] < 2:
                continue
            lons = arr[:, 0]
            lats = arr[:, 1]
            east = np.empty(len(arr))
            north = np.empty(len(arr))
            for i, (lat, lon) in enumerate(zip(lats, lons)):
                east[i], north[i] = wgs84_to_utm(float(lat), float(lon), zone=zone, south=south)
            col = (east - origin_x) / gsd
            row = (origin_y - north) / gsd
            pix = np.column_stack([col * sx, row * sy])
            if pix[:, 0].max() < 0 or pix[:, 1].max() < 0 or pix[:, 0].min() > width or pix[:, 1].min() > height:
                continue
            pix = _simplify(pix, 1.5)
            if len(pix) < 4:
                continue
            span = pix.max(axis=0) - pix.min(axis=0)
            if float(span.min()) < 2.0:
                continue
            features.append(
                {
                    "image_id": image_id,
                    "points": [[round(float(x), 2), round(float(y), 2)] for x, y in pix],
                }
            )
    payload = {
        "stamp": stamp,
        "width": width,
        "height": height,
        "full_width": full_w,
        "full_height": full_h,
        "features": features,
        "preview_ready": True,
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def preview_png(job: JobRun, version: str | None = None) -> bytes:
    payload = build_seam_map(job, version)
    if version and version != "current":
        path = _version_dir(job, version) / "seam_preview.png"
    else:
        path = _extras_dir(job) / "seam_preview.png"
    if not path.is_file() or not payload.get("preview_ready"):
        raise HTTPException(400, "拼接线预览还没有生成")
    return path.read_bytes()


def rgb_ortho_path(job: JobRun) -> Path | None:
    """彩色拼图 Orthomosaic group0。当前成果为空时用最近一次留档。"""
    roots: list[Path] = []
    if getattr(job, "products_dir", None):
        roots.append(Path(job.products_dir))
    out = Path(job.output_dir) if getattr(job, "output_dir", None) else None
    if out is not None:
        roots.append(out / "拼图结果")
        roots.append(out)
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        ortho = root / ORTHO_NAME
        if ortho.is_file():
            return ortho
    if out is None:
        return None
    index = out / "附件" / VERSIONS_DIR / VERSION_INDEX
    if not index.is_file():
        return None
    try:
        rows = json.loads(index.read_text(encoding="utf-8")).get("versions") or []
    except (OSError, json.JSONDecodeError):
        return None
    for row in reversed(rows):
        version = str(row.get("id") or "")
        if not _VERSION_ID.fullmatch(version):
            continue
        ortho = out / "附件" / VERSIONS_DIR / version / ORTHO_NAME
        if ortho.is_file():
            return ortho
    return None


def _rgb_from_overview(ortho: Path) -> bytes:
    overview, _height, _width, _samples, _dtype = _load_tiff_overview(ortho)
    if overview.ndim == 3 and overview.shape[-1] >= 4:
        rgb = np.array(overview[..., :3])
        rgb[overview[..., 3] == 0] = 236
        overview = rgb
    png, _width, _height, _scale = _preview_png(overview, None)
    return png


def rgb_preview_png(job: JobRun) -> bytes:
    """彩色拼图的缩小预览。已有拼接线预览时直接用，避免再读整幅正射。"""
    ortho = rgb_ortho_path(job)
    if ortho is None:
        raise HTTPException(404, "还没有 RGB 拼图影像")
    ready = [
        ortho.parent / "seam_preview.png",
        ortho.parent / "rgb_preview.png",
    ]
    if ortho.parent.name == "拼图结果":
        ready.insert(0, ortho.parent.parent / "附件" / "seam_preview.png")
    for path in ready:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()
    png = _rgb_from_overview(ortho)
    cache = ortho.parent / "rgb_preview.png"
    try:
        cache.write_bytes(png)
    except OSError:
        pass
    return png


def write_seam_edit(job: JobRun, regions: list[dict]) -> str:
    """把预览图上改过的区域写回经纬度，供正射按这些范围锁来源影像。"""
    if not regions:
        raise HTTPException(400, "没有要改的拼接线区域")
    products = _products_dir(job)
    extras = _extras_dir(job)
    extras.mkdir(parents=True, exist_ok=True)
    if not (extras / "seam_preview.json").is_file():
        build_seam_map(job)
    origin_x, origin_y, gsd, full_h, full_w, citation = _georef(products / ORTHO_NAME)
    zone, south = _zone(citation)
    preview = json.loads((extras / "seam_preview.json").read_text(encoding="utf-8"))
    pw = float(preview["width"])
    ph = float(preview["height"])
    features = []
    for region in regions:
        image_id = int(region["image_id"])
        points = region.get("points") or []
        if image_id < 0 or len(points) < 3:
            continue
        ring = []
        for px, py in points:
            col = float(px) * full_w / pw
            row = float(py) * full_h / ph
            east = origin_x + col * gsd
            north = origin_y - row * gsd
            lat, lon = utm_to_wgs84(east, north, zone=zone, south=south)
            ring.append([lon, lat])
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"image_id": image_id},
            }
        )
    if not features:
        raise HTTPException(400, "画的区域无效")
    path = extras / EDIT_NAME
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def clear_seam_edit(job: JobRun) -> None:
    path = _extras_dir(job) / EDIT_NAME
    if path.is_file():
        path.unlink()


_CATALOG: dict[str, tuple[int, dict[int, str]]] = {}


def _at_checkpoint(job: JobRun) -> Path:
    snap = dict(getattr(job, "params_snapshot", None) or {})
    candidates: list[Path] = []
    cache = snap.get("cache_dir") or getattr(job, "cache_dir", None)
    if cache:
        cache_path = Path(str(cache))
        if cache_path.name == "features":
            candidates.append(cache_path.parent / "at_result.npz")
        candidates.append(cache_path / "at_result.npz")
    out = snap.get("output_dir") or getattr(job, "output_dir", None)
    if out:
        candidates.append(Path(str(out)) / "cache" / "at_result.npz")
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    raise HTTPException(400, "还没有空三检查点，无法打开原始影像")


def _image_catalog(job: JobRun) -> dict[int, str]:
    path = _at_checkpoint(job)
    stamp = path.stat().st_mtime_ns
    hit = _CATALOG.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(bytes(data["meta"]).decode("utf-8"))
    ids = [int(i) for i in meta.get("image_ids") or []]
    paths = [str(p) for p in meta.get("image_paths") or []]
    catalog = {i: p for i, p in zip(ids, paths)}
    _CATALOG[str(path)] = (stamp, catalog)
    return catalog


_COVER: dict[str, tuple[int, dict]] = {}


def _coverage_table(job: JobRun) -> dict:
    """彩色影像的外方位和 Color 内参。地面点落在哪张像幅里，用这张表判断。"""
    path = _at_checkpoint(job)
    stamp = path.stat().st_mtime_ns
    hit = _COVER.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(bytes(data["meta"]).decode("utf-8"))
        pose_ids = [int(i) for i in data["pose_ids"]]
        pose_r = np.asarray(data["pose_R"], float)
        pose_c = np.asarray(data["pose_C"], float)
    ids = [int(i) for i in meta.get("image_ids") or []]
    paths = [str(p) for p in meta.get("image_paths") or []]
    pose_of = {i: k for k, i in enumerate(pose_ids)}
    keep = []
    color_ids = []
    names = []
    for image_id, raw in zip(ids, paths):
        if "Color" not in Path(raw).name:
            continue
        slot = pose_of.get(image_id)
        if slot is None:
            continue
        keep.append(slot)
        color_ids.append(image_id)
        names.append(Path(raw).name)
    if not keep:
        raise HTTPException(400, "空三里没有彩色影像")
    cameras = meta.get("cameras") or []
    cam = next((c for c in cameras if c.get("key") == "Color"), None)
    if cam is None:
        raise HTTPException(400, "空三里没有彩色相机内参")
    index = np.asarray(keep, np.int64)
    table = {
        "R": pose_r[index],
        "C": pose_c[index],
        "ids": np.asarray(color_ids, np.int64),
        "names": names,
        "cam": cam,
        "ground_z": float(meta.get("ground_z") or 0.0),
    }
    _COVER[str(path)] = (stamp, table)
    return table


def _preview_to_ground(job: JobRun, xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    products = _products_dir(job)
    origin_x, origin_y, gsd, full_h, full_w, _citation = _georef(products / ORTHO_NAME)
    preview_path = _extras_dir(job) / "seam_preview.json"
    if not preview_path.is_file():
        build_seam_map(job)
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    east = origin_x + xs * full_w / float(preview["width"]) * gsd
    north = origin_y - ys * full_h / float(preview["height"]) * gsd
    return east, north, _coverage_table(job)


def _cover_scores(table: dict, east: np.ndarray, north: np.ndarray) -> np.ndarray:
    """每个地面点、每张彩色影像的像主点偏差。照不到的是 inf。"""
    cam = table["cam"]
    world = np.stack([east, north, np.full(east.shape, table["ground_z"])], axis=1)
    delta = world[:, None, :] - table["C"][None, :, :]
    cam_pts = np.einsum("cij,scj->sci", table["R"], delta)
    depth = cam_pts[:, :, 2]
    valid = depth > 1.0
    safe = np.where(valid, depth, 1.0)
    xn = cam_pts[:, :, 0] / safe
    yn = cam_pts[:, :, 1] / safe
    r2 = xn * xn + yn * yn
    k1, k2, k3 = float(cam["k1"]), float(cam["k2"]), float(cam["k3"])
    p1, p2 = float(cam["p1"]), float(cam["p2"])
    radial = 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))
    xd = xn * radial + (p1 * (r2 + 2.0 * xn * xn) + 2.0 * p2 * xn * yn)
    yd = yn * radial + (p2 * (r2 + 2.0 * yn * yn) + 2.0 * p1 * xn * yn)
    focal = float(cam["f"])
    cx, cy = float(cam["cx"]), float(cam["cy"])
    u = cx + focal * xd + float(cam["b1"]) * xd + float(cam["b2"]) * yd
    v = cy + focal * yd
    width, height = float(cam["width"]), float(cam["height"])
    du = max(cx, width - 1.0 - cx) / max(abs(focal), 1e-6)
    dv = max(cy, height - 1.0 - cy) / max(abs(focal), 1e-6)
    radius = float(np.hypot(du, dv) * 1.15)
    inside = valid & (r2 <= radius * radius) & (u >= 0.0) & (u <= width - 1.0) & (v >= 0.0) & (v <= height - 1.0)
    score = ((u - cx) / width) ** 2 + ((v - cy) / height) ** 2
    return np.where(inside, score, np.inf)


def _ranked_images(table: dict, score: np.ndarray, limit: int | None) -> list[dict]:
    order = np.argsort(score)
    found = []
    stop = len(order) if limit is None else min(len(order), max(1, limit))
    for slot in order[:stop]:
        if not np.isfinite(score[slot]):
            break
        found.append(
            {
                "image_id": int(table["ids"][slot]),
                "name": table["names"][slot],
                "center_offset": round(float(np.sqrt(score[slot])), 3),
            }
        )
    return found


def _ring_contains(ring: np.ndarray, x: float, y: float) -> bool:
    hit = False
    for i, point in enumerate(ring):
        x1, y1 = float(point[0]), float(point[1])
        x2, y2 = float(ring[(i + 1) % len(ring)][0]), float(ring[(i + 1) % len(ring)][1])
        cross = (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 or 1e-9) + x1
        if cross:
            hit = not hit
    return hit


def _polygon_samples(points: np.ndarray) -> np.ndarray:
    """闭合范围的顶点、中心，以及范围内部的采样点。"""
    ring = np.asarray(points, float)
    if ring.ndim != 2 or ring.shape[0] < 3:
        return ring.reshape(-1, 2)
    seeds = [ring, ring.mean(axis=0, keepdims=True)]
    minx, miny = ring.min(axis=0)
    maxx, maxy = ring.max(axis=0)
    span = max(float(maxx - minx), float(maxy - miny), 1.0)
    step = max(span / 8.0, 4.0)
    xs = np.arange(minx, maxx + step * 0.5, step)
    ys = np.arange(miny, maxy + step * 0.5, step)
    inside = [[x, y] for y in ys for x in xs if _ring_contains(ring, float(x), float(y))]
    if inside:
        seeds.append(np.asarray(inside, float))
    samples = np.vstack(seeds)
    if len(samples) > 80:
        pick = np.linspace(0, len(samples) - 1, 80).astype(int)
        samples = samples[pick]
    return samples


def images_covering(job: JobRun, preview_x: float, preview_y: float, *, limit: int = 8) -> list[dict]:
    """预览图上的一点，列出像幅能罩住这块地面的彩色照片，越靠近像主点越靠前。

    投影与正射同一套：世界点经外方位到相机系，Brown 畸变后落到像素。
    """
    east, north, table = _preview_to_ground(job, np.array([preview_x]), np.array([preview_y]))
    score = _cover_scores(table, east, north)[0]
    return _ranked_images(table, score, limit)


def images_covering_polygon(job: JobRun, points: list[list[float]]) -> list[dict]:
    """闭合范围里能拍到的全部彩色影像。任一采样点照得到就算。"""
    ring = np.asarray(points, float)
    if ring.ndim != 2 or ring.shape[0] < 3 or ring.shape[1] < 2:
        return []
    samples = _polygon_samples(ring[:, :2])
    east, north, table = _preview_to_ground(job, samples[:, 0], samples[:, 1])
    best = np.min(_cover_scores(table, east, north), axis=0)
    return _ranked_images(table, best, None)


def source_image_jpeg(job: JobRun, image_id: int, *, max_side: int = 480) -> bytes:
    """把空三里的一张原始影像缩成小图，供选影像时看画面。"""
    max_side = max(160, min(int(max_side), 2000))
    catalog = _image_catalog(job)
    raw = catalog.get(int(image_id))
    if not raw:
        raise HTTPException(404, f"影像 {image_id} 不在这批空三里")
    src = Path(raw)
    if not src.is_file():
        raise HTTPException(404, f"影像 {image_id} 的文件不在磁盘上")
    extras = _extras_dir(job)
    name = f"{int(image_id)}.jpg" if max_side <= 480 else f"{int(image_id)}_{max_side}.jpg"
    cache = extras / "image_thumbs" / name
    try:
        if cache.is_file() and cache.stat().st_mtime_ns >= src.stat().st_mtime_ns:
            return cache.read_bytes()
    except OSError:
        pass
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        data = buf.getvalue()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".jpg.tmp")
    tmp.write_bytes(data)
    tmp.replace(cache)
    return data
