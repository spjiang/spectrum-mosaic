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


def sample_index(errors_px: np.ndarray, n: int) -> np.ndarray:
    """在有效点上按原顺序均匀抽取，同一数组、同一点数每次下标相同。"""
    finite_idx = np.flatnonzero(np.isfinite(np.asarray(errors_px, float)))
    if finite_idx.size == 0:
        raise HTTPException(400, "没有可与 GPS 比较的点")
    take = max(1, min(int(n), int(finite_idx.size), MAX_N))
    if take == finite_idx.size:
        return finite_idx
    pos = np.rint(np.linspace(0, finite_idx.size - 1, take)).astype(int)
    return finite_idx[pos]


def sample_errors(errors_px: np.ndarray, n: int) -> np.ndarray:
    errors = np.asarray(errors_px, float)
    return errors[sample_index(errors, n)]


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


def load_eval_inputs(path: Path) -> dict:
    """读空三检查点：外方位平面、GPS 平面、影像名、坐标系。"""
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
    ids = [int(i) for i in meta.get("image_ids") or []]
    paths = [str(p) for p in meta.get("image_paths") or []]
    by_id = {i: Path(p).name for i, p in zip(ids, paths)}
    names = [by_id.get(int(pid), str(int(pid))) for pid in pose_ids]
    gps_source = "空三检查点里与外方位对齐的 GNSS 平面坐标（同一投影）"
    if gps is None or not np.isfinite(gps).any():
        if not crs or not ids or not paths:
            raise HTTPException(400, "检查点里没有 GPS，也没有影像路径，无法和真实位置比对")
        gps = _gps_from_xmp(paths, ids, pose_ids, crs)
        gps_source = (
            "影像头 XMP 的融合经纬度（PositionLatitudeFused / PositionLongitudeFused），"
            "换到与空三相同的投影平面"
        )
    if not np.isfinite(gps).any():
        raise HTTPException(400, "影像头里没有融合 GPS，无法评估")
    return {
        "pose": pose,
        "gps": gps,
        "crs": crs or "—",
        "pose_ids": pose_ids,
        "names": names,
        "gps_source": gps_source,
    }


def load_pose_and_gps(path: Path) -> tuple[np.ndarray, np.ndarray, str]:
    loaded = load_eval_inputs(path)
    return loaded["pose"], loaded["gps"], loaded["crs"]


_ORTHO_GSD_RE = re.compile(r"拼图分辨率\s+([0-9]*\.?[0-9]+)\s*m/px")


def gsd_from_run_log(text: str) -> float | None:
    """运行日志里的「拼图分辨率」是这次真正用的正射 GSD。取最后一条。"""
    found = _ORTHO_GSD_RE.findall(text or "")
    if not found:
        return None
    value = float(found[-1])
    return value if value > 0 else None


def ortho_gsd_explain(job_gsd: float | None, snap: dict, log_text: str | None = None) -> tuple[float, str]:
    """拼图分辨率，以及这句话里的数从哪来。"""
    raw = snap.get("ortho_gsd_m")
    if raw not in (None, ""):
        value = float(raw)
        return value, f"任务参数里写明的拼图分辨率 {value:.6f} m/px"
    if job_gsd not in (None, 0):
        value = float(job_gsd)
        return value, f"任务记录里的拼图分辨率 {value:.6f} m/px"
    dsm = snap.get("dsm_gsd")
    if dsm not in (None, ""):
        value = float(dsm) * 0.5
        return value, f"任务参数 DSM 分辨率 {float(dsm):.6f} m 的一半，即 {value:.6f} m/px"
    logged = gsd_from_run_log(log_text or "")
    if logged is not None:
        return logged, f"运行日志「拼图分辨率 {logged:.6f} m/px」"
    raise HTTPException(400, "没有拼图分辨率，无法把平面偏差换成像素")


def ortho_gsd_of(job_gsd: float | None, snap: dict, log_text: str | None = None) -> float:
    value, _source = ortho_gsd_explain(job_gsd, snap, log_text)
    return value


def req_05_01_report() -> dict:
    """REQ-05-01 只认野外 GPS 真值。机载定位和相机中心的差不能当成这一项。"""
    return {
        "requirement": "REQ-05-01",
        "title": "多波段对齐精度验收标准",
        "accepted": False,
        "message": "验收结论：不具备验收条件。未提供野外真实 GPS 真值坐标，不能按 REQ-05-01 完成验收。",
        "clauses": [
            {
                "no": "1",
                "text": "每一个光谱波段均匀选取 20~30 个检测点位",
                "result": "未执行",
                "detail": "须按每一个光谱波段分别均匀选取 20~30 个检测点位。当前未提供检测点位，本条未执行。",
            },
            {
                "no": "2",
                "text": "与野外真实 GPS 真值坐标比对",
                "result": "未执行",
                "detail": "比对基准为野外真实 GPS 真值坐标。机载定位与空三相机中心残差不属于本项真值，不得替代。当前未提供野外真实 GPS 真值坐标，本条未执行。",
            },
            {
                "no": "3",
                "text": "平均位置误差严格小于 2 像素，保障多波段配准精度一致性",
                "result": "不能判定",
                "detail": "判定条件为平均位置误差严格小于 2 像素。等于 2 像素不视为满足。因第 2 条未执行，本条不能判定。",
            },
            {
                "no": "4",
                "text": "误差统计口径、GSD 像素换算基准、布点规则、GPS 真值精度等级",
                "result": "需求未给定",
                "detail": "上述四项在需求中标注为 TBD。未给定前不另行规定，不据此出具通过结论。",
            },
        ],
    }


def build_report(
    pose_xy: np.ndarray,
    gps_xy: np.ndarray,
    pose_ids: np.ndarray,
    names: list[str],
    *,
    n: int,
    gsd_m: float,
    gsd_source: str,
    crs: str,
    gps_source: str,
    sigma_xy_m: float | None = None,
    threshold_px: float = THRESHOLD_PX,
) -> dict:
    """抽查点的平面偏差。汇总值只按抽中的点计算，并给出每个点的代入过程。"""
    pose = np.asarray(pose_xy, float)
    gps = np.asarray(gps_xy, float)
    if pose.shape != gps.shape or pose.ndim != 2 or pose.shape[1] != 2:
        raise HTTPException(400, "外方位与 GPS 无法对齐")
    if gsd_m <= 0:
        raise HTTPException(400, "拼图分辨率无效，无法把米换算成像素")
    de = pose[:, 0] - gps[:, 0]
    dn = pose[:, 1] - gps[:, 1]
    dist = np.hypot(de, dn)
    bad = ~np.isfinite(pose).all(axis=1) | ~np.isfinite(gps).all(axis=1)
    dist = dist.astype(float)
    dist[bad] = np.nan
    errors = dist / float(gsd_m)
    picked = sample_index(errors, n)
    chosen = errors[picked]
    over = int(np.sum(chosen > threshold_px))
    rms = float(np.sqrt(np.mean(chosen**2)))
    worst = float(np.max(chosen))
    mean = float(np.mean(chosen))
    valid = np.isfinite(errors)
    n_valid = int(valid.sum())
    n_valid_over = int(np.sum(errors[valid] > threshold_px))
    points = []
    worst_name = ""
    for row in picked.tolist():
        px = float(errors[row])
        name = names[row] if row < len(names) else str(int(pose_ids[row]))
        if px == worst:
            worst_name = name
        points.append(
            {
                "image_id": int(pose_ids[row]),
                "name": name,
                "solved_e": float(pose[row, 0]),
                "solved_n": float(pose[row, 1]),
                "gps_e": float(gps[row, 0]),
                "gps_n": float(gps[row, 1]),
                "de_m": float(de[row]),
                "dn_m": float(dn[row]),
                "dist_m": float(dist[row]),
                "error_px": px,
                "over": bool(px > threshold_px),
            }
        )
    ok = over == 0
    note = (
        f"抽查 {chosen.size} 张，全部在 {threshold_px:g} px 以内，最大 {worst:.2f} px（{worst_name}）"
        if ok
        else f"抽查 {chosen.size} 张，有 {over} 张超过 {threshold_px:g} px，最大 {worst:.2f} px（{worst_name}）"
    )
    return {
        "n_requested": int(n),
        "n_used": int(chosen.size),
        "n_valid": n_valid,
        "n_valid_over": n_valid_over,
        "threshold_px": float(threshold_px),
        "threshold_m": float(threshold_px) * float(gsd_m),
        "rms_px": rms,
        "max_px": worst,
        "mean_px": mean,
        "n_over": over,
        "within_tolerance": ok,
        "gsd_m": float(gsd_m),
        "gsd_source": gsd_source,
        "crs": crs,
        "gps_source": gps_source,
        "sigma_xy_m": None if sigma_xy_m is None else float(sigma_xy_m),
        "worst_name": worst_name,
        "message": note,
        "points": points,
    }
