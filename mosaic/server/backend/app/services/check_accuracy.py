"""多波段正射的野外检测点验收。

用户在基准波段正射上选点，软件给出该点的拼图坐标。用户再填入野外 GPS。
像素误差 = 拼图坐标与野外坐标的平面距离 / 基准波段 GSD。检测点不进入空三。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.models import JobRun
from app.services.geodesy import utm_to_wgs84, utm_zone_from_epsg, wgs84_to_utm
from app.services.inspect_media import _decode_segment, _preview_png, _segment_index, _tiff_layout
from app.services.seam_map import _georef, _zone
from app.services.stage_steps import read_run_log

STORE_NAME = "accuracy_check.json"
GROUP_RE = re.compile(r"^Orthomosaic_pix_surf_group(\d+)\.tif$", re.IGNORECASE)
WROTE_RE = re.compile(r"已写出\s+Orthomosaic_pix_surf_group(\d+)\.tif（(.+?)\s*完成）")
WINDOW = 720
DEFAULT_LABELS = ("Color", "450nm", "550nm", "650nm", "720nm", "750nm", "800nm", "850nm")
HEADER_ALIAS = {
    "点名": "name",
    "name": "name",
    "id": "name",
    "经度": "x",
    "东坐标": "x",
    "经度或东坐标": "x",
    "x": "x",
    "easting": "x",
    "lon": "x",
    "longitude": "x",
    "纬度": "y",
    "北坐标": "y",
    "纬度或北坐标": "y",
    "y": "y",
    "northing": "y",
    "lat": "y",
    "latitude": "y",
    "高程": "z",
    "z": "z",
    "h": "z",
    "height": "z",
    "elevation": "z",
    "坐标系": "crs",
    "crs": "crs",
    "epsg": "crs",
}


def _num(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _blank() -> dict[str, Any]:
    return {
        "min_count": 20,
        "max_count": 30,
        "max_mean_px": 2.0,
        "crs": "EPSG:4326",
        "reference_band": "",
        "evaluated": False,
        "points": [],
    }


def store_path(job: JobRun) -> Path:
    out = getattr(job, "output_dir", None)
    if not out:
        raise HTTPException(400, "任务还没有输出目录")
    return Path(out) / "附件" / STORE_NAME


def load_doc(job: JobRun) -> dict[str, Any]:
    path = store_path(job)
    if not path.is_file():
        return _blank()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "检测点记录读不出来") from exc
    base = _blank()
    base.update({k: data.get(k, base[k]) for k in base})
    # 旧数据把野外坐标当成选点。新流程的点必须带拼图像素，没有像素的记录丢掉。
    kept = []
    for item in base.get("points") or []:
        if not isinstance(item, dict) or item.get("col") is None or item.get("row") is None or not item.get("name"):
            continue
        kept.append(
            {
                "name": str(item["name"]),
                "col": float(item["col"]),
                "row": float(item["row"]),
                "gps_x": None if item.get("gps_x") is None else float(item["gps_x"]),
                "gps_y": None if item.get("gps_y") is None else float(item["gps_y"]),
                "gps_z": None if item.get("gps_z") is None else float(item["gps_z"]),
            }
        )
    base["points"] = kept
    base["evaluated"] = bool(base.get("evaluated")) and bool(kept)
    if not base.get("crs"):
        base["crs"] = "EPSG:4326"
    return base


def save_doc(job: JobRun, doc: dict[str, Any]) -> None:
    path = store_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def template_csv() -> str:
    return (
        "点名,经度,纬度,高程,坐标系\n"
        "CP01,114.1234567,22.5432100,12.350,EPSG:4326\n"
    )


def _normalize_crs(text: str) -> str:
    raw = text.strip().upper().replace(" ", "")
    if raw.startswith("EPSG:"):
        code = raw.split(":", 1)[1]
    elif raw.isdigit():
        code = raw
    else:
        raise HTTPException(400, f"坐标系无法识别：{text}")
    if not code.isdigit():
        raise HTTPException(400, f"坐标系无法识别：{text}")
    epsg = int(code)
    if epsg == 4326 or 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760:
        return f"EPSG:{epsg}"
    raise HTTPException(400, "坐标系只接受 EPSG:4326 或 UTM 投影")


def parse_csv(raw: bytes) -> tuple[str, list[dict[str, Any]]]:
    """读检测点表。地理坐标时经度、纬度；投影坐标时两列分别为东、北。"""
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text.strip():
        raise HTTPException(400, "检测点表是空的")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "检测点表缺少表头")
    fields: dict[str, str] = {}
    for name in reader.fieldnames:
        if not name:
            continue
        key = HEADER_ALIAS.get(name.strip()) or HEADER_ALIAS.get(name.strip().lower())
        if key:
            fields[key] = name
    missing = [label for key, label in (("name", "点名"), ("x", "经度"), ("y", "纬度"), ("crs", "坐标系")) if key not in fields]
    if missing:
        raise HTTPException(400, "检测点表缺少列：" + "、".join(missing))
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    crs = ""
    for line_no, row in enumerate(reader, start=2):
        if not row or not any((value or "").strip() for value in row.values() if value is not None):
            continue
        name = (row.get(fields["name"]) or "").strip()
        if not name:
            raise HTTPException(400, f"第 {line_no} 行没有点名")
        if name in seen:
            raise HTTPException(400, f"点名重复：{name}")
        seen.add(name)
        try:
            x = float(str(row.get(fields["x"])).strip())
            y = float(str(row.get(fields["y"])).strip())
            z_text = (row.get(fields["z"]) or "").strip() if "z" in fields else ""
            z = float(z_text) if z_text else 0.0
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"第 {line_no} 行坐标不是数字") from exc
        row_crs = _normalize_crs(str(row.get(fields["crs"]) or ""))
        if crs and row_crs != crs:
            raise HTTPException(400, "同一张表里的坐标系必须相同")
        crs = row_crs
        points.append({"name": name, "x": x, "y": y, "z": z})
    if not points:
        raise HTTPException(400, "检测点表没有数据行")
    if len(points) > 500:
        raise HTTPException(400, "检测点超过 500 个")
    return crs, points


def check_config(min_count: int, max_count: int, max_mean_px: float) -> None:
    if min_count < 1 or max_count < 1 or min_count > 500 or max_count > 500:
        raise HTTPException(400, "检测点数量须在 1～500")
    if min_count > max_count:
        raise HTTPException(400, "最少点数不能大于最多点数")
    if not (0 < float(max_mean_px) <= 1000):
        raise HTTPException(400, "平均位置误差上限须大于 0")


def apply_config(doc: dict[str, Any], min_count: int, max_count: int, max_mean_px: float) -> None:
    check_config(min_count, max_count, max_mean_px)
    doc["min_count"] = int(min_count)
    doc["max_count"] = int(max_count)
    doc["max_mean_px"] = float(max_mean_px)


def products_dir(job: JobRun) -> Path | None:
    candidates = []
    if getattr(job, "products_dir", None):
        candidates.append(Path(job.products_dir))
    out = getattr(job, "output_dir", None)
    if out:
        candidates.append(Path(out) / "拼图结果")
        candidates.append(Path(out))
    for folder in candidates:
        if folder.is_dir() and any(GROUP_RE.match(path.name) for path in folder.iterdir() if path.is_file()):
            return folder
    return None


def band_labels(job: JobRun) -> dict[int, str]:
    text = read_run_log(getattr(job, "output_dir", None), getattr(job, "log_dir", None))
    found: dict[int, str] = {}
    for match in WROTE_RE.finditer(text or ""):
        found[int(match.group(1))] = match.group(2).strip()
    return found


def list_bands(job: JobRun) -> list[dict[str, Any]]:
    folder = products_dir(job)
    if folder is None:
        return []
    labels = band_labels(job)
    rows = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        match = GROUP_RE.match(path.name)
        if not match:
            continue
        index = int(match.group(1))
        label = labels.get(index) or (DEFAULT_LABELS[index] if index < len(DEFAULT_LABELS) else f"波段 {index}")
        rows.append({"id": f"group{index}", "index": index, "label": label, "path": path})
    rows.sort(key=lambda item: item["index"])
    return rows


def band_by_id(job: JobRun, band_id: str) -> dict[str, Any]:
    for band in list_bands(job):
        if band["id"] == band_id:
            return band
    raise HTTPException(404, "没有这个波段的正射")


def ortho_epsg(citation: str) -> str:
    zone, south = _zone(citation)
    return f"EPSG:{32700 + zone if south else 32600 + zone}"


def to_map(x: float, y: float, crs: str, target_epsg: str) -> tuple[float, float]:
    """检测点坐标转到正射投影。地理坐标的 x 是经度、y 是纬度。"""
    source = _normalize_crs(crs)
    target = _normalize_crs(target_epsg)
    if source == "EPSG:4326":
        zone, south = utm_zone_from_epsg(target)
        return wgs84_to_utm(y, x, zone=zone, south=south)
    if source == target:
        return float(x), float(y)
    raise HTTPException(400, f"检测点坐标系 {source} 与正射 {target} 不一致")


def map_from_pixel(col: float, row: float, origin_x: float, origin_y: float, gsd: float) -> tuple[float, float]:
    return origin_x + float(col) * gsd, origin_y - float(row) * gsd


def pixel_from_map(east: float, north: float, origin_x: float, origin_y: float, gsd: float) -> tuple[float, float]:
    return (float(east) - origin_x) / gsd, (origin_y - float(north)) / gsd


def _georef_of(path: Path) -> dict[str, Any]:
    origin_x, origin_y, gsd, height, width, citation = _georef(path)
    if gsd <= 0:
        raise HTTPException(400, f"{path.name} 没有有效的地面分辨率")
    return {
        "origin_x": origin_x,
        "origin_y": origin_y,
        "gsd": gsd,
        "height": height,
        "width": width,
        "epsg": ortho_epsg(citation),
    }


def resolve_reference(doc: dict[str, Any], bands: list[dict[str, Any]]) -> str:
    """基准波段默认 Color。同一曝光各波段几何共用，检测点只在基准波段刺一次。"""
    ids = {band["id"] for band in bands}
    wanted = str(doc.get("reference_band") or "")
    if wanted in ids:
        return wanted
    for band in bands:
        if band["label"] == "Color" or band["id"] == "group0":
            return band["id"]
    return bands[0]["id"] if bands else ""


def _marked(mark: Any) -> bool:
    return isinstance(mark, dict) and mark.get("col") is not None and mark.get("row") is not None


def _clause(no: str, text: str, result: str, detail: str) -> dict[str, str]:
    return {"no": no, "text": text, "result": result, "detail": detail}


def _next_name(points: list[dict[str, Any]]) -> str:
    used = set()
    for point in points:
        match = re.fullmatch(r"CP(\d+)", str(point.get("name") or ""))
        if match:
            used.add(int(match.group(1)))
    number = 1
    while number in used:
        number += 1
    return f"CP{number:02d}"


def _gps_filled(point: dict[str, Any]) -> bool:
    return point.get("gps_x") is not None and point.get("gps_y") is not None


def build_state(job: JobRun) -> dict[str, Any]:
    """拼图坐标随时可看。平面误差和验收结论只在点击评估校验之后给出。"""
    doc = load_doc(job)
    min_count = int(doc["min_count"])
    max_count = int(doc["max_count"])
    limit = float(doc["max_mean_px"])
    points = list(doc.get("points") or [])
    bands = list_bands(job)
    reference_id = resolve_reference(doc, bands)
    ref = next((band for band in bands if band["id"] == reference_id), None)
    geo = None
    if ref is not None:
        try:
            geo = _georef_of(ref["path"])
        except HTTPException:
            geo = None
    zone = south = None
    if geo is not None:
        zone, south = utm_zone_from_epsg(geo["epsg"])
    rows = []
    errors: list[float] = []
    for point in points:
        east = north = lon = lat = None
        dist = err = None
        gps_col = gps_row = None
        if geo is not None:
            east, north = map_from_pixel(point["col"], point["row"], geo["origin_x"], geo["origin_y"], geo["gsd"])
            lat, lon = utm_to_wgs84(east, north, zone=zone, south=south)
        filled = _gps_filled(point)
        if geo is not None and filled and doc.get("crs"):
            try:
                field_e, field_n = to_map(float(point["gps_x"]), float(point["gps_y"]), str(doc["crs"]), geo["epsg"])
            except HTTPException:
                field_e = field_n = None
            if field_e is not None and east is not None:
                gps_col, gps_row = pixel_from_map(field_e, field_n, geo["origin_x"], geo["origin_y"], geo["gsd"])
                if doc.get("evaluated"):
                    dist = ((field_e - east) ** 2 + (field_n - north) ** 2) ** 0.5
                    err = dist / geo["gsd"]
                    errors.append(err)
        rows.append(
            {
                "name": point["name"],
                "col": point["col"],
                "row": point["row"],
                "east": east,
                "north": north,
                "lon": lon,
                "lat": lat,
                "gps_x": point.get("gps_x"),
                "gps_y": point.get("gps_y"),
                "gps_z": point.get("gps_z"),
                "gps_col": gps_col,
                "gps_row": gps_row,
                "dist_m": dist,
                "err_px": err,
            }
        )
    count = len(rows)
    count_ok = min_count <= count <= max_count
    filled_n = sum(1 for row in rows if row["gps_x"] is not None and row["gps_y"] is not None)
    complete = bool(points) and filled_n == count and geo is not None
    mean = sum(errors) / len(errors) if doc.get("evaluated") and errors and len(errors) == count else None
    maximum = max(errors) if errors and len(errors) == count else None
    means_ok = bool(mean is not None and mean < limit)
    limit_text = _num(limit)
    range_text = f"{min_count}～{max_count}"
    ref_label = ref["label"] if ref else "基准波段"
    evaluated = bool(doc.get("evaluated"))
    if not evaluated:
        clause_result = "未评估"
    else:
        clause_result = None
    clauses = [
        _clause(
            "1",
            f"检测点数量为 {range_text} 个",
            clause_result or ("满足" if count_ok and count else "不满足"),
            f"当前在拼图上选择 {count} 个检测点，配置范围为 {range_text} 个。",
        ),
        _clause(
            "2",
            f"基准波段 {ref_label} 的拼图坐标与野外 GPS 坐标比对",
            clause_result or ("满足" if complete else "未完成"),
            "每个检测点先在正射上选出，列表给出该点的拼图坐标，再填入野外 GPS 坐标。",
        ),
        _clause(
            "3",
            f"平均位置误差严格小于 {limit_text} 像素",
            clause_result or ("满足" if means_ok else ("不满足" if complete else "未完成")),
            f"像素误差 = 拼图坐标与野外坐标的平面距离 / 基准波段地面分辨率。平均值为算术平均。等于 {limit_text} 像素判定为不满足。",
        ),
    ]
    if not evaluated:
        if not points:
            message = "请在拼图上选择检测点。选完后填写野外 GPS 坐标，再点击评估校验。"
        else:
            message = f"已选择 {count} 个检测点。请填写野外 GPS 坐标后，点击评估校验。"
        accepted = False
    elif not points:
        message = "验收结论：不具备验收条件。未在拼图上选择检测点。"
        accepted = False
    elif geo is None:
        message = "验收结论：不具备验收条件。还没有正射成果。"
        accepted = False
    elif not count_ok:
        message = f"验收结论：不通过。检测点数量不在 {range_text} 个范围内。"
        accepted = False
    elif not complete:
        message = "验收结论：不具备验收条件。尚有检测点未填写野外 GPS 坐标。"
        accepted = False
    elif not means_ok:
        message = f"验收结论：不通过。平均位置误差未严格小于 {limit_text} 像素。"
        accepted = False
    else:
        message = f"验收结论：通过。平均位置误差严格小于 {limit_text} 像素。"
        accepted = True
    return {
        "min_count": min_count,
        "max_count": max_count,
        "max_mean_px": limit,
        "crs": doc.get("crs") or "EPSG:4326",
        "reference_band": reference_id,
        "ortho_epsg": "" if geo is None else geo["epsg"],
        "gsd_m": None if geo is None else geo["gsd"],
        "evaluated": evaluated,
        "mean_px": mean,
        "max_px": maximum,
        "points": rows,
        "bands": [
            {
                "id": band["id"],
                "label": band["label"],
                "reference": band["id"] == reference_id,
                "gsd_m": None if geo is None or band["id"] != reference_id else geo["gsd"],
            }
            for band in bands
        ],
        "accepted": accepted,
        "message": message,
        "clauses": clauses,
    }


def _touch(doc: dict[str, Any]) -> None:
    doc["evaluated"] = False


def add_point(job: JobRun, col: float, row: float) -> dict[str, Any]:
    doc = load_doc(job)
    bands = list_bands(job)
    reference_id = resolve_reference(doc, bands)
    if not reference_id:
        raise HTTPException(400, "还没有正射成果，不能选点")
    geo = _georef_of(band_by_id(job, reference_id)["path"])
    if not (0 <= float(col) < geo["width"] and 0 <= float(row) < geo["height"]):
        raise HTTPException(400, "选点超出影像范围")
    if len(doc["points"]) >= int(doc["max_count"]):
        raise HTTPException(400, f"检测点已达到最多 {doc['max_count']} 个")
    doc["reference_band"] = reference_id
    doc["points"].append(
        {"name": _next_name(doc["points"]), "col": float(col), "row": float(row), "gps_x": None, "gps_y": None, "gps_z": None}
    )
    _touch(doc)
    save_doc(job, doc)
    return build_state(job)


def move_point(job: JobRun, name: str, col: float, row: float) -> dict[str, Any]:
    doc = load_doc(job)
    point = next((item for item in doc["points"] if item["name"] == name), None)
    if point is None:
        raise HTTPException(400, "没有这个检测点")
    reference_id = resolve_reference(doc, list_bands(job))
    geo = _georef_of(band_by_id(job, reference_id)["path"])
    if not (0 <= float(col) < geo["width"] and 0 <= float(row) < geo["height"]):
        raise HTTPException(400, "选点超出影像范围")
    point["col"] = float(col)
    point["row"] = float(row)
    _touch(doc)
    save_doc(job, doc)
    return build_state(job)


def delete_point(job: JobRun, name: str) -> dict[str, Any]:
    doc = load_doc(job)
    kept = [item for item in doc["points"] if item["name"] != name]
    if len(kept) == len(doc["points"]):
        raise HTTPException(400, "没有这个检测点")
    doc["points"] = kept
    _touch(doc)
    save_doc(job, doc)
    return build_state(job)


def set_gps(
    job: JobRun,
    name: str,
    x: float | None,
    y: float | None,
    z: float | None,
    crs: str | None = None,
) -> dict[str, Any]:
    doc = load_doc(job)
    point = next((item for item in doc["points"] if item["name"] == name), None)
    if point is None:
        raise HTTPException(400, "没有这个检测点")
    if crs:
        doc["crs"] = _normalize_crs(crs)
    if x is None or y is None:
        point["gps_x"] = None
        point["gps_y"] = None
        point["gps_z"] = None
    else:
        point["gps_x"] = float(x)
        point["gps_y"] = float(y)
        point["gps_z"] = None if z is None else float(z)
    _touch(doc)
    save_doc(job, doc)
    return build_state(job)


def run_evaluation(job: JobRun) -> dict[str, Any]:
    doc = load_doc(job)
    doc["evaluated"] = True
    save_doc(job, doc)
    return build_state(job)


def update_config(
    job: JobRun,
    min_count: int,
    max_count: int,
    max_mean_px: float,
    reference_band: str | None = None,
) -> dict[str, Any]:
    doc = load_doc(job)
    apply_config(doc, min_count, max_count, max_mean_px)
    bands = list_bands(job)
    if reference_band:
        ids = {band["id"] for band in bands}
        if reference_band not in ids:
            raise HTTPException(400, "没有这个基准波段")
        previous = resolve_reference(doc, bands)
        if previous and previous != reference_band and doc["points"]:
            old_geo = _georef_of(band_by_id(job, previous)["path"])
            new_geo = _georef_of(band_by_id(job, reference_band)["path"])
            for point in doc["points"]:
                east, north = map_from_pixel(point["col"], point["row"], old_geo["origin_x"], old_geo["origin_y"], old_geo["gsd"])
                point["col"], point["row"] = pixel_from_map(east, north, new_geo["origin_x"], new_geo["origin_y"], new_geo["gsd"])
            _touch(doc)
        doc["reference_band"] = reference_band
    save_doc(job, doc)
    return build_state(job)


def read_window(path: Path, col0: int, row0: int, win_w: int, win_h: int):
    """按条带或瓦片读取正射窗口，不把整幅读进内存。"""
    import tifffile
    import numpy as np

    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        layout = _tiff_layout(page)
        full_h = int(layout["height"])
        full_w = int(layout["width"])
        win_w = max(1, min(int(win_w), full_w))
        win_h = max(1, min(int(win_h), full_h))
        col0 = min(max(0, int(col0)), full_w - win_w)
        row0 = min(max(0, int(row0)), full_h - win_h)
        samples = int(layout["samples"])
        if samples == 1:
            out = np.zeros((win_h, win_w), dtype=page.dtype)
        else:
            out = np.zeros((win_h, win_w, samples), dtype=page.dtype)
        th = int(layout["th"])
        tw = int(layout["tw"])
        cache: dict[int, Any] = {}
        fh = tf.filehandle
        for row in range(row0, row0 + win_h):
            block = (col0 // tw) * tw
            while block < col0 + win_w:
                index = _segment_index(layout, row, block)
                if index not in cache:
                    cache[index] = _decode_segment(page, fh, index)
                seg = cache[index]
                if seg is not None:
                    local_r = row % th
                    if local_r < seg.shape[0]:
                        c_start = max(col0, block)
                        c_end = min(col0 + win_w, block + tw, full_w)
                        local_c0 = c_start - block
                        dest_c0 = c_start - col0
                        dest_c1 = dest_c0 + (c_end - c_start)
                        if seg.ndim == 2:
                            out[row - row0, dest_c0:dest_c1] = seg[local_r, local_c0 : local_c0 + (c_end - c_start)]
                        else:
                            out[row - row0, dest_c0:dest_c1] = seg[local_r, local_c0 : local_c0 + (c_end - c_start)]
                block += tw
        return out, col0, row0


def window_png(
    job: JobRun,
    band_id: str,
    cx: float | None,
    cy: float | None,
    size: int | None = None,
) -> tuple[bytes, dict[str, float]]:
    """按整幅像素中心打开正射窗口，不要求已经有检测点。"""
    bands = list_bands(job)
    if band_id:
        band = band_by_id(job, band_id)
    elif bands:
        band = band_by_id(job, resolve_reference(load_doc(job), bands))
    else:
        raise HTTPException(400, "还没有正射成果")
    geo = _georef_of(band["path"])
    center_c = geo["width"] / 2 if cx is None else float(cx)
    center_r = geo["height"] / 2 if cy is None else float(cy)
    side = WINDOW if size is None else max(32, min(int(size), WINDOW))
    half = side // 2
    raw, origin_c, origin_r = read_window(
        band["path"],
        int(round(center_c - half)),
        int(round(center_r - half)),
        side,
        side,
    )
    view = raw
    if getattr(raw, "ndim", 0) == 3 and raw.shape[-1] >= 4:
        rgb = raw[..., :3].copy()
        rgb[raw[..., 3] == 0] = 236
        view = rgb
    png, width, height, _scale = _preview_png(view, None)
    meta = {
        "origin_col": float(origin_c),
        "origin_row": float(origin_r),
        "width": float(width),
        "height": float(height),
        "full_width": float(geo["width"]),
        "full_height": float(geo["height"]),
        "gsd_m": float(geo["gsd"]),
    }
    return png, meta


def overview_png(job: JobRun, band_id: str) -> tuple[bytes, dict[str, float]]:
    """整幅缩略图，点击后只用于把大图跳到对应位置。"""
    import io

    from PIL import Image

    from app.services.seam_map import _rgb_from_overview

    bands = list_bands(job)
    if band_id:
        band = band_by_id(job, band_id)
    elif bands:
        band = band_by_id(job, resolve_reference(load_doc(job), bands))
    else:
        raise HTTPException(400, "还没有正射成果")
    geo = _georef_of(band["path"])
    ready = band["path"].with_name("rgb_preview.png")
    if band["id"] == "group0" and ready.is_file():
        png = ready.read_bytes()
    else:
        cache = store_path(job).with_name(f"accuracy_overview_{band['id']}.png")
        stamp = cache.with_suffix(".json")
        mtime = int(band["path"].stat().st_mtime)
        png = b""
        if cache.is_file() and stamp.is_file():
            try:
                saved = json.loads(stamp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = {}
            if saved.get("mtime") == mtime:
                png = cache.read_bytes()
        if not png:
            png = _rgb_from_overview(band["path"])
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(png)
            stamp.write_text(json.dumps({"mtime": mtime}), encoding="utf-8")
    with Image.open(io.BytesIO(png)) as image:
        preview_w, preview_h = image.size
    return png, {
        "width": float(preview_w),
        "height": float(preview_h),
        "full_width": float(geo["width"]),
        "full_height": float(geo["height"]),
    }


def browse_jpeg(job: JobRun, band_id: str) -> tuple[bytes, dict[str, float]]:
    """整幅正射压成一张图，供页面本地拖动和缩放，拖动过程不再读盘。"""
    import io

    from PIL import Image

    from app.services.inspect_media import _load_tiff_overview

    bands = list_bands(job)
    if band_id:
        band = band_by_id(job, band_id)
    elif bands:
        band = band_by_id(job, resolve_reference(load_doc(job), bands))
    else:
        raise HTTPException(400, "还没有正射成果")
    geo = _georef_of(band["path"])
    cache = store_path(job).with_name(f"accuracy_browse_{band['id']}.jpg")
    stamp = cache.with_suffix(".json")
    mtime = int(band["path"].stat().st_mtime)
    data = b""
    if cache.is_file() and stamp.is_file():
        try:
            saved = json.loads(stamp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        if saved.get("mtime") == mtime:
            data = cache.read_bytes()
    if not data:
        import numpy as np

        overview, _height, _width, _samples, _dtype = _load_tiff_overview(band["path"], 4096)
        if overview.ndim == 3 and overview.shape[-1] >= 4:
            rgb = np.array(overview[..., :3])
            rgb[overview[..., 3] == 0] = 236
            overview = rgb
        elif overview.ndim == 3:
            overview = np.array(overview[..., :3])
        if overview.dtype != np.uint8:
            planes = [overview] if overview.ndim == 2 else [overview[..., i] for i in range(overview.shape[-1])]
            stretched = []
            for plane in planes:
                vals = plane.astype(np.float64)
                finite = np.isfinite(vals)
                if not finite.any():
                    stretched.append(np.zeros(plane.shape, np.uint8))
                    continue
                lo, hi = np.percentile(vals[finite], [2, 98])
                if hi <= lo:
                    lo, hi = float(vals[finite].min()), float(vals[finite].max()) or 1.0
                out = np.zeros(plane.shape, np.uint8)
                out[finite] = np.clip((vals[finite] - lo) / max(hi - lo, 1e-9) * 255.0, 0, 255).astype(np.uint8)
                stretched.append(out)
            overview = stretched[0] if len(stretched) == 1 else np.stack(stretched, axis=-1)
        image = Image.fromarray(np.ascontiguousarray(overview))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        data = buffer.getvalue()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        stamp.write_text(json.dumps({"mtime": mtime}), encoding="utf-8")
    with Image.open(io.BytesIO(data)) as image:
        preview_w, preview_h = image.size
    return data, {
        "width": float(preview_w),
        "height": float(preview_h),
        "full_width": float(geo["width"]),
        "full_height": float(geo["height"]),
    }
