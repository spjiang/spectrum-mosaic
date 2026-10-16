"""空三结果落盘，续跑不再重做特征匹配与平差。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ms_mosaic.at import ATResult
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.progress import stage_index
from ms_mosaic.runner import SparseResult
from ms_mosaic.scene import Block
from ms_mosaic.tracks import Tracks

_CAM_FIELDS = ("key", "width", "height", "f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2", "model")
AT_RESULT_NAME = "at_result.npz"


def _json_default(value: Any):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def checkpoint_path(cache_dir: Path | str) -> Path:
    cache_dir = Path(cache_dir)
    parent = cache_dir.parent if cache_dir.name == "features" else cache_dir
    return parent / AT_RESULT_NAME


def should_reuse_at(start_stage: str | None, path: Path) -> bool:
    start = start_stage or "S0_io"
    try:
        after_at = stage_index(start) > stage_index("S2_at")
    except ValueError:
        after_at = False
    try:
        exists = path.is_file() and path.stat().st_size > 0
    except OSError:
        exists = False
    return after_at and exists


def _cam_dict(cam: Camera) -> dict[str, Any]:
    return {name: getattr(cam, name) for name in _CAM_FIELDS}


def _cam_from_dict(data: dict[str, Any]) -> Camera:
    return Camera(**{name: data[name] for name in _CAM_FIELDS})


def save_sparse(
    path: Path,
    sparse: SparseResult,
    *,
    max_index: int | None = None,
    max_frames: int | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pose_ids = [int(i) for i in sorted(sparse.poses)]
    if pose_ids:
        pose_r = np.stack([np.asarray(sparse.poses[i].rotation, float) for i in pose_ids])
        pose_c = np.stack([np.asarray(sparse.poses[i].center, float) for i in pose_ids])
    else:
        pose_r = np.zeros((0, 3, 3), float)
        pose_c = np.zeros((0, 3), float)
    cameras = [_cam_dict(cam) for cam in sparse.at.cameras.values()] or [_cam_dict(sparse.camera)]
    image_ids = [int(i) for i in sorted(sparse.image_paths)]
    meta = {
        "schema": 1,
        "max_index": max_index,
        "max_frames": max_frames,
        "crs": sparse.block.crs,
        "ground_z": sparse.block.ground_z,
        "stats": sparse.at.stats,
        "timings": sparse.timings,
        "counts": sparse.counts,
        "cameras": cameras,
        "image_ids": image_ids,
        "image_paths": [str(sparse.image_paths[i]) for i in image_ids],
        "camera_key": sparse.camera.key,
    }
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(
        tmp,
        meta=np.frombuffer(json.dumps(meta, ensure_ascii=False, default=_json_default).encode("utf-8"), dtype=np.uint8),
        points=np.asarray(sparse.at.points, float),
        residuals=np.asarray(sparse.at.residuals_px, float),
        pose_R=pose_r,
        pose_C=pose_c,
        pose_ids=np.asarray(pose_ids, np.int64),
        image_ids=np.asarray(image_ids, np.int64),
    )
    tmp.replace(path)
    return path


def load_at_payload(path: Path) -> dict[str, Any]:
    with np.load(Path(path), allow_pickle=False) as data:
        meta = json.loads(bytes(data["meta"]).decode("utf-8"))
        pose_ids = [int(i) for i in data["pose_ids"]]
        pose_r = np.asarray(data["pose_R"], float)
        pose_c = np.asarray(data["pose_C"], float)
        poses = {
            idx: Pose(rotation=pose_r[k], center=pose_c[k]) for k, idx in enumerate(pose_ids)
        }
        cameras = {_cam_from_dict(item).key: _cam_from_dict(item) for item in meta.get("cameras") or []}
        image_ids = [int(i) for i in (data["image_ids"] if "image_ids" in data.files else meta.get("image_ids") or [])]
        paths = meta.get("image_paths") or []
        image_paths = {idx: Path(p) for idx, p in zip(image_ids, paths)}
        return {
            **meta,
            "points": np.asarray(data["points"], float),
            "residuals_px": np.asarray(data["residuals"], float) if "residuals" in data.files else np.zeros(0),
            "poses": poses,
            "camera_objs": cameras,
            "image_path_map": image_paths,
        }


def apply_payload(block: Block, payload: dict[str, Any]) -> SparseResult:
    cameras = dict(payload.get("camera_objs") or {})
    for key, cam in cameras.items():
        block.cameras[key] = cam
    poses = dict(payload.get("poses") or {})
    for idx, pose in poses.items():
        block.poses[idx] = pose
    if payload.get("crs"):
        block.crs = str(payload["crs"])
    if payload.get("ground_z") is not None:
        block.ground_z = float(payload["ground_z"])
    primary_key = payload.get("camera_key") or next(iter(cameras), "Color")
    primary = cameras.get(primary_key) or next(iter(cameras.values()))
    residuals = payload.get("residuals_px")
    if residuals is None:
        residuals = []
    at = ATResult(
        cameras=cameras,
        poses=poses,
        points=np.asarray(payload["points"], float),
        observations=[],
        residuals_px=np.asarray(residuals, float),
        stats=dict(payload.get("stats") or {}),
    )
    image_paths = dict(payload.get("image_path_map") or {})
    if not image_paths:
        # 全波段空三检查点应带齐各波段路径；旧检查点无路径时按 block 全影像回填
        image_paths = {im.index: Path(im.path) for im in block.images}
    return SparseResult(
        block=block,
        at=at,
        tracks=Tracks(observations=[]),
        image_paths=image_paths,
        poses=poses,
        camera=primary,
        timings=dict(payload.get("timings") or {}),
        counts=dict(payload.get("counts") or {}),
        matches=[],
        keypoint_counts={},
    )


def rebuild_block(
    input_dir: Path,
    *,
    max_index: int | None = None,
    max_frames: int | None = None,
    min_agl_m: float | None = None,
    max_tilt_deg: float | None = None,
    drop_white_panel: bool = True,
    require_pos: bool = True,
) -> Block:
    from ms_mosaic.catalog import scan_directory
    from ms_mosaic.scene import MIN_AGL_M, MAX_TILT_DEG, build_block, usable_shots

    shots = scan_directory(Path(input_dir), max_index=max_index)
    keep, _ = usable_shots(
        shots,
        min_agl_m=MIN_AGL_M if min_agl_m is None else float(min_agl_m),
        max_tilt_deg=MAX_TILT_DEG if max_tilt_deg is None else float(max_tilt_deg),
        drop_white_panel=drop_white_panel,
        require_pos=require_pos,
    )
    if max_frames is not None:
        keys = sorted(keep)[: int(max_frames)]
        keep = {k: keep[k] for k in keys}
    return build_block(keep)


def resolve_sparse(
    input_dir: Path,
    cache_dir: Path,
    *,
    start_stage: str | None = None,
    max_index: int | None = None,
    max_frames: int | None = None,
    workers: int | None = None,
    memory_gb: float | None = None,
    min_agl_m: float | None = None,
    max_tilt_deg: float | None = None,
    drop_white_panel: bool = True,
    require_pos: bool = True,
    sigma_xy_m: float | None = None,
    sigma_z_m: float | None = None,
    sigma_attitude_deg: float | None = None,
    outlier_threshold_px: float | None = None,
    calibrate_intrinsics: bool = True,
    log: Callable[[str], None] | None = print,
    run_sparse_fn=None,
    rebuild_fn: Callable[..., Block] | None = None,
) -> SparseResult:
    from ms_mosaic.runner import run_sparse as default_run

    run_sparse_fn = run_sparse_fn or default_run
    path = checkpoint_path(cache_dir)
    if should_reuse_at(start_stage, path):
        if log:
            log(f"复用空三检查点 {path}")
        payload = load_at_payload(path)
        mi = max_index if max_index is not None else payload.get("max_index")
        mf = max_frames if max_frames is not None else payload.get("max_frames")
        builder = rebuild_fn or rebuild_block
        block = builder(
            input_dir,
            max_index=mi,
            max_frames=mf,
            min_agl_m=min_agl_m,
            max_tilt_deg=max_tilt_deg,
            drop_white_panel=drop_white_panel,
            require_pos=require_pos,
        )
        return apply_payload(block, payload)
    sparse_kw: dict[str, Any] = {
        "max_index": max_index,
        "max_frames": max_frames,
        "workers": workers or 10,
        "memory_gb": memory_gb,
        "drop_white_panel": drop_white_panel,
        "require_pos": require_pos,
        "calibrate_intrinsics": calibrate_intrinsics,
        "log": log,
    }
    if min_agl_m is not None:
        sparse_kw["min_agl_m"] = min_agl_m
    if max_tilt_deg is not None:
        sparse_kw["max_tilt_deg"] = max_tilt_deg
    if sigma_xy_m is not None:
        sparse_kw["sigma_xy_m"] = sigma_xy_m
    if sigma_z_m is not None:
        sparse_kw["sigma_z_m"] = sigma_z_m
    if sigma_attitude_deg is not None:
        sparse_kw["sigma_attitude_deg"] = sigma_attitude_deg
    if outlier_threshold_px is not None:
        sparse_kw["outlier_threshold_px"] = outlier_threshold_px
    sparse = run_sparse_fn(input_dir, cache_dir, **sparse_kw)
    save_sparse(path, sparse, max_index=max_index, max_frames=max_frames)
    if log:
        log(f"已写入空三检查点 {path}")
    return sparse
