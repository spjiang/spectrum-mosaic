"""任务成果的查看与下载。

只开放本次输出里的拼图原文件和质量报告，不把缓存、日志打包出去。
下载链接是短时票据，浏览器直接拉文件，不把整包读进页面。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.models import JobRun
from app.services.check_accuracy import list_bands, products_dir
from app.services.jobs import locate_report_pdf

FIGURES = ("ortho", "dsm", "camera_pos", "at_view", "overlap")
_TICKETS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_TICKET_SECONDS = 300


def _output(job: JobRun) -> Path:
    out = getattr(job, "output_dir", None)
    if not out:
        raise HTTPException(400, "任务还没有输出目录")
    return Path(out)


def _bytes(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def catalog(job: JobRun) -> dict[str, Any]:
    """列出可查看的波段和可下载的原文件。"""
    bands = []
    originals = []
    for band in list_bands(job):
        path = Path(band["path"])
        item = {
            "id": band["id"],
            "label": band["label"],
            "name": path.name,
            "bytes": _bytes(path),
        }
        bands.append(item)
        originals.append({"name": path.name, "label": band["label"], "bytes": item["bytes"]})
    folder = products_dir(job)
    if folder is not None:
        dsm = folder / "DSM.tif"
        if dsm.is_file():
            originals.append({"name": dsm.name, "label": "DSM", "bytes": _bytes(dsm)})
    return {
        "bands": bands,
        "originals": originals,
        "has_report": _quality_json(job).is_file() or locate_report_pdf(job) is not None,
    }


def _quality_json(job: JobRun) -> Path:
    return _output(job) / "report" / "quality.json"


def _num(value: Any, digits: int | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if digits is None:
        return number
    return round(number, digits)


def quality_view(job: JobRun) -> dict[str, Any]:
    """质量报告里给人看的汇总。不返回每张照片的明细和服务器路径。"""
    path = _quality_json(job)
    pdf = locate_report_pdf(job)
    if not path.is_file():
        return {
            "ready": False,
            "has_pdf": pdf is not None,
            "message": "质量报告尚未生成。" if pdf is None else "只有 PDF，还没有可展示的汇总数据。",
            "figures": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "质量报告无法读取") from exc
    at = payload.get("at") or {}
    dsm = payload.get("dsm") or {}
    ortho = payload.get("ortho") or {}
    features = payload.get("features") or {}
    matching = payload.get("matching") or {}
    cameras = []
    for name, item in (payload.get("cameras") or {}).items():
        if not isinstance(item, dict):
            continue
        cameras.append(
            {
                "band": str(name),
                "group": item.get("group"),
                "n_images": item.get("n_images"),
                "n_registered": item.get("n_registered"),
                "rms_px": _num(item.get("rms_px"), 3),
                "width": item.get("width"),
                "height": item.get("height"),
                "f": _num(item.get("f"), 2),
            }
        )
    cameras.sort(key=lambda row: (row["group"] is None, row["group"] if isinstance(row["group"], int) else 99, str(row["band"])))
    figures = []
    fig_dir = path.parent / "figures"
    for name in FIGURES:
        if (fig_dir / f"{name}.png").is_file():
            figures.append(name)
    histogram = []
    for row in payload.get("gps_histogram") or []:
        if not isinstance(row, dict):
            continue
        histogram.append(
            {
                "label": row.get("label"),
                "error": _num(row.get("error"), 2),
                "dx": _num(row.get("dx"), 2),
                "dy": _num(row.get("dy"), 2),
                "dz": _num(row.get("dz"), 2),
            }
        )
    return {
        "ready": True,
        "has_pdf": pdf is not None,
        "message": "",
        "project_name": payload.get("project_name") or "",
        "created_at": payload.get("created_at") or "",
        "crs_label": payload.get("crs_label") or payload.get("crs") or "",
        "n_scanned": payload.get("n_scanned"),
        "n_shots": payload.get("n_shots"),
        "n_images": payload.get("n_images"),
        "n_registered": payload.get("n_registered"),
        "n_filtered": payload.get("n_filtered"),
        "area_km2": _num(payload.get("area_km2"), 3),
        "mean_agl_m": _num(payload.get("mean_agl_m"), 2),
        "elapsed_s": _num(payload.get("elapsed_s"), 1),
        "at": {
            "rms_reprojection_px": _num(at.get("rms_reprojection_px"), 3),
            "n_points": at.get("n_points"),
            "n_observations": at.get("n_observations"),
            "gps_rmse_m": _num(at.get("gps_rmse_m"), 3),
            "gps_rmse_x_m": _num(at.get("gps_rmse_x_m"), 3),
            "gps_rmse_y_m": _num(at.get("gps_rmse_y_m"), 3),
            "gps_rmse_z_m": _num(at.get("gps_rmse_z_m"), 3),
            "n_gps": at.get("n_gps"),
        },
        "dsm": {
            "gsd": _num(dsm.get("gsd"), 4),
            "width": dsm.get("width"),
            "height": dsm.get("height"),
            "fill_ratio": _num(dsm.get("fill_ratio"), 3),
            "z_min": _num(dsm.get("z_min"), 2),
            "z_median": _num(dsm.get("z_median"), 2),
            "z_max": _num(dsm.get("z_max"), 2),
        },
        "ortho": {
            "gsd": _num(ortho.get("gsd"), 4),
            "mode": ortho.get("mode") or "",
            "blend": ortho.get("blend") or "",
        },
        "features": {
            "mean_keypoints": _num(features.get("mean_keypoints"), 1),
            "min_keypoints": features.get("min_keypoints"),
            "max_keypoints": features.get("max_keypoints"),
            "n_poor": features.get("n_poor"),
        },
        "matching": {
            "n_tracks": matching.get("n_tracks"),
            "mean_tracks": _num(matching.get("mean_tracks"), 1),
            "n_matched_pairs": matching.get("n_matched_pairs"),
        },
        "cameras": cameras,
        "histogram": histogram,
        "figures": figures,
    }


def figure_file(job: JobRun, name: str) -> Path:
    if name not in FIGURES:
        raise HTTPException(404, "没有这张附图")
    path = (_output(job) / "report" / "figures" / f"{name}.png").resolve()
    root = (_output(job) / "report" / "figures").resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "这张附图还没有")
    return path


def original_file(job: JobRun, name: str) -> Path:
    """只允许拼图目录里的 GeoTIFF 原文件。"""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "文件名不合法")
    if not name.lower().endswith(".tif"):
        raise HTTPException(400, "原文件只提供 GeoTIFF")
    folder = products_dir(job)
    if folder is None:
        raise HTTPException(404, "还没有拼图成果")
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise HTTPException(404, "没有这个原文件")
    return path


def _archive_members(job: JobRun, kind: str) -> list[tuple[Path, str]]:
    folder = products_dir(job)
    members: list[tuple[Path, str]] = []
    if folder is not None:
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if kind == "original" and path.suffix.lower() != ".tif":
                continue
            if kind == "all" and path.suffix.lower() not in {".tif", ".png"}:
                continue
            members.append((path, f"拼图结果/{path.name}"))
    if kind == "all":
        pdf = locate_report_pdf(job)
        if pdf is not None:
            members.append((pdf, "质量报告.pdf"))
        report = _output(job) / "report"
        if report.is_dir():
            for path in sorted(report.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".json", ".md", ".png"}:
                    continue
                members.append((path, str(Path("report") / path.relative_to(report))))
    if not members:
        raise HTTPException(404, "还没有可下载的成果")
    return members


def build_archive(job: JobRun, kind: str) -> tuple[Path, str]:
    if kind not in {"original", "all"}:
        raise HTTPException(400, "下载范围不正确")
    members = _archive_members(job, kind)
    folder = _output(job) / "附件" / ".downloads"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("*.zip"):
        try:
            if now - old.stat().st_mtime > 7200:
                old.unlink()
        except OSError:
            continue
    dest = folder / f"{kind}-{secrets.token_hex(8)}.zip"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, arcname in members:
            archive.write(path, arcname)
    seq = getattr(job, "seq", None)
    prefix = f"任务{seq}_" if seq else ""
    filename = prefix + ("拼图原文件.zip" if kind == "original" else "全部成果.zip")
    return dest, filename


def drop_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def issue_ticket(job_id: str, kind: str, name: str | None) -> str:
    if kind not in {"original", "all", "file"}:
        raise HTTPException(400, "下载范围不正确")
    if kind == "file" and not name:
        raise HTTPException(400, "请指定要下载的原文件")
    token = secrets.token_urlsafe(18)
    with _LOCK:
        _prune_tickets()
        _TICKETS[token] = {
            "exp": time.time() + _TICKET_SECONDS,
            "job_id": job_id,
            "kind": kind,
            "name": name or "",
        }
    return token


def read_ticket(token: str, job_id: str) -> dict[str, Any]:
    with _LOCK:
        _prune_tickets()
        row = _TICKETS.get(token)
    if row is None or row["job_id"] != job_id:
        raise HTTPException(401, "下载链接已失效，请重新点击下载")
    return row


def _prune_tickets() -> None:
    now = time.time()
    stale = [key for key, row in _TICKETS.items() if row["exp"] < now]
    for key in stale:
        _TICKETS.pop(key, None)
