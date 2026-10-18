"""空三外方位相对 GNSS 的平面偏差，用拼图像素表达。

偏差（米）/ 拼图 GSD = 像素。默认抽 20 个点，标准 2 px。
点在全部有效点上均匀抽取，同一检查点每次结果相同。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from app.services.geodesy import utm_zone_from_epsg, wgs84_to_utm

THRESHOLD_PX = 2.0
DEFAULT_N = 20
MAX_N = 500
_LAT_RE = re.compile(r"<Camera:PositionLatitudeFused>([^<]+)</Camera:PositionLatitudeFused>")
_LON_RE = re.compile(r"<Camera:PositionLongitudeFused>([^<]+)</Camera:PositionLongitudeFused>")


def sample_errors(errors_px: np.ndarray, n: int) -> np.ndarray:
    finite = np.asarray(errors_px, float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise HTTPException(400, "没有可与 GPS 比较的点")
    take = max(1, min(int(n), int(finite.size), MAX_N))
    if take == finite.size:
        return finite
    idx = np.linspace(0, finite.size - 1, take)
    return finite[np.rint(idx).astype(int)]


def summarize(errors_px: np.ndarray, *, n: int, gsd_m: float, threshold_px: float = THRESHOLD_PX) -> dict:
    chosen = sample_errors(errors_px, n)
    over = int(np.sum(chosen > threshold_px))
    rms = float(np.sqrt(np.mean(chosen**2)))
    worst = float(np.max(chosen))
    ok = over == 0
    note = (
        f"{chosen.size} 个点全部在 {threshold_px:g} px 以内，最大 {worst:.2f} px"
        if ok
        else f"{chosen.size} 个点里有 {over} 个超过 {threshold_px:g} px，最大 {worst:.2f} px"
    )
    return {
        "n_requested": int(n),
        "n_used": int(chosen.size),
        "threshold_px": float(threshold_px),
        "rms_px": rms,
        "max_px": worst,
        "mean_px": float(np.mean(chosen)),
        "n_over": over,
        "within_tolerance": ok,
        "gsd_m": float(gsd_m),
        "message": note,
    }


def pixel_errors(solved_xy: np.ndarray, gps_xy: np.ndarray, gsd_m: float) -> np.ndarray:
    if gsd_m <= 0:
        raise HTTPException(400, "拼图分辨率无效，无法把米换算成像素")
    solved = np.asarray(solved_xy, float)
    gps = np.asarray(gps_xy, float)
    if solved.shape != gps.shape or solved.ndim != 2 or solved.shape[1] != 2:
        raise HTTPException(400, "外方位与 GPS 无法对齐")
    dist = np.hypot(solved[:, 0] - gps[:, 0], solved[:, 1] - gps[:, 1])
    bad = ~np.isfinite(solved).all(axis=1) | ~np.isfinite(gps).all(axis=1)
    dist[bad] = np.nan
    return dist / float(gsd_m)


def _gps_from_xmp(paths: list[str], ids: list[int], pose_ids: np.ndarray, crs: str) -> np.ndarray:
    zone, south = utm_zone_from_epsg(crs)
    by_id = {int(i): path for i, path in zip(ids, paths)}
    out = np.full((len(pose_ids), 2), np.nan, float)
    for row, pid in enumerate(pose_ids):
        path = by_id.get(int(pid))
        if not path:
            continue
        try:
            blob = Path(path).read_bytes()[:1024 * 1024]
        except OSError:
            continue
        text = blob.decode("latin1", errors="ignore")
        lat_m = _LAT_RE.search(text)
        lon_m = _LON_RE.search(text)
        if lat_m is None or lon_m is None:
            continue
        try:
            lat = float(lat_m.group(1))
            lon = float(lon_m.group(1))
        except ValueError:
            continue
        out[row] = wgs84_to_utm(lat, lon, zone=zone, south=south)
    return out


def load_pose_and_gps(path: Path) -> tuple[np.ndarray, np.ndarray, str]:
    if not path.is_file():
        raise HTTPException(400, "还没有空三检查点，跑完空三后才能评估")
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(bytes(data["meta"]).decode("utf-8"))
            pose = np.asarray(data["pose_C"], float)[:, :2]
            pose_ids = np.asarray(data["pose_ids"], np.int64)
            gps = np.asarray(data["gps_xy"], float) if "gps_xy" in data.files else None
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(400, f"空三检查点读不出来: {exc}") from exc
    crs = str(meta.get("crs") or "")
    if gps is None or not np.isfinite(gps).any():
        ids = [int(i) for i in meta.get("image_ids") or []]
        paths = [str(p) for p in meta.get("image_paths") or []]
        if not crs or not ids or not paths:
            raise HTTPException(400, "检查点里没有 GPS，也没有影像路径，无法和真实位置比对")
        gps = _gps_from_xmp(paths, ids, pose_ids, crs)
    if not np.isfinite(gps).any():
        raise HTTPException(400, "影像头里没有融合 GPS，无法评估")
    return pose, gps, crs


def ortho_gsd_of(job_gsd: float | None, snap: dict) -> float:
    for key in ("ortho_gsd_m",):
        raw = snap.get(key)
        if raw not in (None, ""):
            return float(raw)
    if job_gsd not in (None, 0):
        return float(job_gsd)
    dsm = snap.get("dsm_gsd")
    if dsm not in (None, ""):
        return float(dsm) * 0.5
    raise HTTPException(400, "没有拼图分辨率，无法把平面偏差换成像素")
