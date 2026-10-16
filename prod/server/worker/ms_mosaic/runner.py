"""稀疏重建的编排层：扫描 → 像对 → 特征 → 匹配 → 构网 → 空三。

单独成模块是因为特征提取和匹配要跑多进程，工作函数必须能被 pickle，
不能是闭包或局部函数。脚本与正式管线都调这里，避免两份实现走偏。
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ms_mosaic.at import ATResult, run_at
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.catalog import scan_directory
from ms_mosaic.features import extract_file, load_keypoints
from ms_mosaic.matching import PairMatches, match_pair
from ms_mosaic.cpu import cpu_cap, job_cpus
from ms_mosaic.memory import resolve_memory_budget, visible_limit_bytes
from ms_mosaic.pairs import select_pairs
from ms_mosaic.at import CALIBRATION_STAGES
from ms_mosaic.scene import (
    BAND_ORDER,
    MIN_AGL_M,
    MAX_TILT_DEG,
    PRIMARY_BAND,
    SIGMA_ATTITUDE_DEG,
    SIGMA_XY_M,
    SIGMA_Z_M,
    Block,
    build_block,
    usable_shots,
)
from ms_mosaic.tracks import Tracks, build_tracks

_SHARED: dict = {}

# 单进程 SIFT 实测约 300MB；留余量避免 12 进程把 Docker 虚拟机打满后被 oom_kill。
_AT_WORKER_MB = 450
_AT_RESERVE_MB = 1024


def _cpu_count() -> int:
    return os.cpu_count() or 1


def _available_memory_bytes() -> int | None:
    cgroup_max = Path("/sys/fs/cgroup/memory.max")
    cgroup_cur = Path("/sys/fs/cgroup/memory.current")
    try:
        raw = cgroup_max.read_text(encoding="utf-8").strip()
        if raw not in {"", "max"}:
            limit = int(raw)
            used = int(cgroup_cur.read_text(encoding="utf-8").strip()) if cgroup_cur.is_file() else 0
            return max(0, limit - used)
    except (OSError, ValueError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def cap_at_workers(
    requested: int,
    *,
    per_worker_mb: int = _AT_WORKER_MB,
    reserve_mb: int = _AT_RESERVE_MB,
    budget_bytes: int | None = None,
) -> int:
    """按 CPU 和内存预算把空三进程数压到安全范围。

    budget_bytes：任务上手动指定的内存（例如 36GB）。有预算时不再看容器当前 MemAvailable。
    """
    n = max(1, int(requested or 1))
    budget = job_cpus()
    if budget is not None and budget > 0:
        cpu_limit = cpu_cap()
    else:
        cpus = _cpu_count()
        cpu_limit = max(1, cpus - 1 if cpus > 1 else 1)
        if budget_bytes is None:
            cpu_limit = min(cpu_limit, 8)
    if budget_bytes is None:
        avail = _available_memory_bytes()
    else:
        avail = max(0, int(budget_bytes))
    if avail is None:
        mem_cap = cpu_limit
    else:
        usable = max(0, avail - reserve_mb * 1024 * 1024)
        mem_cap = max(1, usable // (per_worker_mb * 1024 * 1024))
    return max(1, min(n, cpu_limit, mem_cap))


def _limit_blas_threads() -> None:
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(var, "1")


def _extract_init() -> None:
    _limit_blas_threads()


@dataclass
class SparseResult:
    block: Block
    at: ATResult
    tracks: Tracks
    image_paths: dict[int, Path]
    poses: dict[int, Pose]
    camera: Camera
    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    matches: list[PairMatches] = field(default_factory=list)
    keypoint_counts: dict[int, int] = field(default_factory=dict)

    @property
    def points(self) -> np.ndarray:
        return self.at.points


def _extract_worker(job):
    index, path, cache_dir, max_features = job
    extract_file(Path(path), cache_dir=Path(cache_dir), max_features=max_features)
    return index


def _match_init(cache_dir, cameras_by_band: dict):
    _limit_blas_threads()
    _SHARED["cameras"] = cameras_by_band
    _SHARED["cache_dir"] = str(cache_dir)


def _match_worker(job):
    i, j, path_i, path_j, band = job
    cam = _SHARED["cameras"][band]
    cache = Path(_SHARED["cache_dir"])
    fi = extract_file(Path(path_i), cache_dir=cache)
    fj = extract_file(Path(path_j), cache_dir=cache)
    pm = match_pair(i, j, fi, fj, cam, cam)
    if pm is None:
        return None
    return (pm.i, pm.j, pm.indices, pm.model)


def _band_pair_candidates(block: Block, ground_z: float) -> list:
    """按波段组内选像对，对齐 LiMapper「关联影像序列」按组连通的做法。"""
    from ms_mosaic.pairs import PairCandidate

    seen: set[tuple[int, int]] = set()
    out: list[PairCandidate] = []
    for band in BAND_ORDER:
        band_ims = block.by_band(band)
        if len(band_ims) < 2 or band not in block.cameras:
            continue
        cam = block.cameras[band]
        cameras = {im.index: cam for im in band_ims}
        poses = {im.index: block.poses[im.index] for im in band_ims}
        for cand in select_pairs(cameras, poses, ground_z):
            key = (cand.i, cand.j)
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
    out.sort(key=lambda p: -p.overlap)
    return out


def run_sparse(
    input_dir: Path,
    cache_dir: Path,
    *,
    max_index: int | None = None,
    max_frames: int | None = None,
    max_features: int = 8192,
    workers: int = 10,
    memory_gb: float | None = None,
    min_agl_m: float = MIN_AGL_M,
    max_tilt_deg: float = MAX_TILT_DEG,
    drop_white_panel: bool = True,
    require_pos: bool = True,
    sigma_xy_m: float = SIGMA_XY_M,
    sigma_z_m: float = SIGMA_Z_M,
    sigma_attitude_deg: float = SIGMA_ATTITUDE_DEG,
    outlier_threshold_px: float | None = None,
    calibrate_intrinsics: bool = True,
    log=lambda *_: None,
) -> SparseResult:
    """全波段稀疏重建（特征/匹配/空三），对齐商业八组都参与。输入目录只读。"""
    timings: dict[str, float] = {}
    t0 = time.time()

    shots = scan_directory(input_dir, max_index=max_index)
    keep, reasons = usable_shots(
        shots,
        min_agl_m=min_agl_m,
        max_tilt_deg=max_tilt_deg,
        drop_white_panel=drop_white_panel,
        require_pos=require_pos,
    )
    if max_frames is not None:
        keys = sorted(keep)[: int(max_frames)]
        keep = {k: keep[k] for k in keys}
    block = build_block(keep)
    images = list(block.images)
    primary = block.primary()
    log(f"曝光 {len(shots)} → 可用 {len(keep)}（过滤 {reasons}），影像 {len(images)} 张（含各波段）")

    poses = {im.index: block.poses[im.index] for im in images}
    candidates = _band_pair_candidates(block, block.ground_z)
    n_full = len(images) * (len(images) - 1) // 2
    log(
        f"候选像对 {len(candidates)} 对（按 {len(BAND_ORDER)} 个波段组内选对；"
        f"若全影像两两组合为 {n_full}）"
    )

    cache_dir.mkdir(parents=True, exist_ok=True)
    indices = [im.index for im in images]
    paths = [str(im.path) for im in images]
    band_by = {im.index: im.band for im in images}
    requested = workers
    budget_bytes = resolve_memory_budget(memory_gb)
    try:
        gb = float(memory_gb) if memory_gb is not None else 0.0
    except (TypeError, ValueError):
        gb = 0.0
    if budget_bytes is not None:
        limit = visible_limit_bytes()
        if limit is not None and gb > 0 and int(gb * 1024**3) > limit:
            log(
                f"任务内存 {gb:g} GB 超过 Docker 引擎上限 {limit / 1024**3:.1f} GB，"
                "已按引擎上限封顶。配高不会多分到内存。"
            )
        elif gb > 0:
            log(f"任务内存预算 {gb:g} GB")
        else:
            log(f"任务内存按引擎上限 {budget_bytes / 1024**3:.1f} GB")
    cpu_budget = job_cpus()
    if cpu_budget:
        log(f"任务 CPU 预算 {cpu_budget} 核（引擎可见 {cpu_cap()}）")
    workers = cap_at_workers(workers, budget_bytes=budget_bytes)
    if workers != requested:
        bits = []
        if gb > 0:
            bits.append(f"内存 {gb:g} GB")
        if cpu_budget:
            bits.append(f"CPU {cpu_budget} 核")
        why = "、".join(bits) if bits else "可用内存/CPU"
        log(f"空三并行下调 {requested} → {workers}（按{why}封顶）")
    else:
        log(f"空三并行 {workers} 进程")
    ctx = multiprocessing.get_context("spawn")

    t = time.time()
    jobs = [(i, p, str(cache_dir), max_features) for i, p in zip(indices, paths)]
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_extract_init) as pool:
        list(pool.map(_extract_worker, jobs))
    timings["features"] = time.time() - t
    log(f"特征提取 {len(jobs)} 张，{timings['features']:.1f}s")

    t = time.time()
    path_by = dict(zip(indices, paths))
    pair_jobs = [
        (c.i, c.j, path_by[c.i], path_by[c.j], band_by[c.i]) for c in candidates
    ]
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_match_init,
        initargs=(str(cache_dir), dict(block.cameras)),
    ) as pool:
        raw = list(pool.map(_match_worker, pair_jobs, chunksize=4))
    matches = [PairMatches(*r) for r in raw if r is not None]
    timings["matching"] = time.time() - t
    log(f"匹配 {len(matches)}/{len(candidates)} 对成功，{timings['matching']:.1f}s")

    tracks = build_tracks(matches)
    # 像对列表交完轨即可丢，避免与交会点云叠在一起占内存。
    del matches
    gc.collect()
    log(
        f"连接点 {len(tracks)} 条，观测 {tracks.total_observations()}，"
        f"平均轨迹长 {tracks.lengths.mean():.2f}"
    )

    # 交会只要关键点。不加载描述子，峰值大约少 8 GB（1992×8192×128 float32）。
    keypoints = {
        i: load_keypoints(Path(p), cache_dir=cache_dir) for i, p in zip(indices, paths)
    }
    image_camera = {im.index: im.band for im in images}
    gps = {im.index: block.gps[im.index] for im in images}
    att = {im.index: block.poses[im.index].rotation for im in images}

    t = time.time()
    at_stages = CALIBRATION_STAGES if calibrate_intrinsics else ((),)
    at = run_at(
        dict(block.cameras),
        poses,
        tracks,
        keypoints,
        image_camera,
        gps,
        att,
        sigma_xyz=(sigma_xy_m, sigma_xy_m, sigma_z_m),
        sigma_attitude_deg=sigma_attitude_deg,
        stages=at_stages,
        outlier_threshold_px=outlier_threshold_px if outlier_threshold_px is not None else 6.0,
        log=log,
    )
    del keypoints
    gc.collect()
    timings["at"] = time.time() - t
    timings["total"] = time.time() - t0
    log(f"空三 {timings['at']:.1f}s，稀疏重建合计 {timings['total']:.1f}s")

    # 密集/GSD 仍取主波段内参；缺 Color 时退回任一已解相机
    primary_cam = at.cameras.get(PRIMARY_BAND) or next(iter(at.cameras.values()))
    return SparseResult(
        block=block,
        at=at,
        tracks=tracks,
        image_paths={im.index: im.path for im in images},
        poses=at.poses,
        camera=primary_cam,
        timings=timings,
        counts={
            "n_shots": len(shots),
            "n_usable": len(keep),
            "n_primary": len(primary),
            "n_images": len(images),
            "n_candidates": len(candidates),
            "n_matched_pairs": len(matches),
            "n_tracks": len(tracks),
            "n_keypoints": int(sum(len(feats[i]) for i in feats)),
        },
        matches=matches,
        keypoint_counts={i: len(feats[i]) for i in feats},
    )
