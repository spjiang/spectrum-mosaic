from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ms_mosaic.compose import render_band, write_band_product
from ms_mosaic.parallel import resolve_workers, split_ortho_pools
from ms_mosaic.dense import DenseConfig, compute_height_field
from ms_mosaic.dsm import (
    Dsm,
    MAX_FILL_GAP_M,
    build_dsm,
    complete_dsm_coverage,
    flatten_edge_z,
    write_geotiff,
)
from ms_mosaic.grid import (
    ORTHO_OVER_DSM,
    Grid,
    coverage_from_footprints,
    erode_coverage,
    estimate_dsm_gsd,
    estimate_z_margin_m,
    grid_from_footprints,
    ground_reference_z,
    smooth_coverage_mask,
)
from ms_mosaic.products import (
    EXTRAS_DIRNAME,
    PRODUCTS_DIRNAME,
    REPORT_PDF_NAME,
    RGB_BAND,
    group_name,
    match_lowfreq_to_reference,
    scale_mosaic_to_reference,
    seamline_geojson,
    write_kml,
    write_pseudocolor,
)
from ms_mosaic.qa import (
    band_rms_px,
    build_quality_payload,
    cameras_table,
    registered_image_ids,
    render_report_figures,
)
from ms_mosaic.report import write_report
from ms_mosaic.report_pdf import write_quality_pdf
from ms_mosaic.control import ControlState
from ms_mosaic.progress import NullReporter, ProgressReporter, global_percent_for, stage_index
from ms_mosaic.scene import BAND_ORDER, PRIMARY_BAND, transfer_band

COMMERCIAL_PRODUCTS = "拼图结果"
_log_lock = threading.Lock()


def _barrier(control: ControlState, reporter: ProgressReporter, stage: str) -> dict[str, Any] | None:
    flag = control.checkpoint_barrier(wait=False)
    if flag == "cancel":
        reporter.event("cancelled", stage_id=stage)
        return {"status": "cancelled", "completed_stage": stage}
    if flag == "pause":
        reporter.event("paused", stage_id=stage, message="已暂停，可继续运行")
        flag2 = control.checkpoint_barrier(wait=True)
        if flag2 == "cancel":
            reporter.event("cancelled", stage_id=stage)
            return {"status": "cancelled", "completed_stage": stage}
        reporter.event("running", stage_id=stage, message="已继续运行")
    return None


def _stop_here(stop_after: str | None, stage: str, payload: dict[str, Any], reporter: ProgressReporter) -> dict[str, Any] | None:
    if stop_after is None:
        return None
    if stage_index(stage) < stage_index(stop_after):
        return None
    reporter.event("awaiting_continue", stage_id=stage, message=f"{stage} 完成，等待继续")
    out = dict(payload)
    out["status"] = "awaiting_continue"
    out["completed_stage"] = stage
    return out


def _assert_out_outside_input(input_dir: Path, out_dir: Path) -> tuple[Path, Path]:
    """输入目录只读。成果必须写到输入目录之外。"""
    inp = input_dir.expanduser().resolve()
    out = out_dir.expanduser().resolve()
    if out == inp or inp in out.parents:
        raise ValueError(f"禁止写入输入目录 {inp}，请把 --output-dir 指到别处（例如 runs/）")
    return inp, out


def commercial_product_dir(input_dir: Path) -> Path | None:
    """查找输入目录上一级的「拼图结果」。主路径不自动调用，仅供显式对标。"""
    cand = Path(input_dir).expanduser().resolve().parent / COMMERCIAL_PRODUCTS
    return resolve_benchmark_dir(cand, required=False)


def resolve_grid_reference(path: Path | str | None) -> tuple[Path, Path | None] | None:
    """解析「锁定格网」的参考成果，返回 (DSM 路径, 正射路径或 None)。

    交付格网（GSD、原点、宽高）往往是任务书给定的规格，而不是由影像反推。
    给了参考成果就直接采用它的 transform，逐像元对比才有意义 —— 自动估计的
    GSD 与商业成品差 13%，原点也差半个像元，重采样后灰度相关系数只有 0.2，
    那个数字量的是格网错位而不是影像质量。

    只锁格网，不碰高程、覆盖与颜色；那些仍由本次影像自己算出来。
    """
    if path is None:
        return None
    cand = Path(path).expanduser().resolve()
    if cand.is_file():
        return cand, None
    dsm = cand / "DSM.tif"
    if not dsm.is_file():
        raise ValueError(f"锁定格网需要 DSM.tif：{cand}")
    ortho = cand / "Orthomosaic_pix_surf_group0.tif"
    return dsm, (ortho if ortho.is_file() else None)


def resolve_benchmark_dir(path: Path | str | None, *, required: bool = True) -> Path | None:
    """验收目录必须同时有 DSM.tif 和 RGB 正射。不传则走通用足迹格网。"""
    if path is None:
        return None
    cand = Path(path).expanduser().resolve()
    dsm = cand / "DSM.tif"
    rgb = cand / "Orthomosaic_pix_surf_group0.tif"
    if dsm.is_file() and rgb.is_file():
        return cand
    if required:
        raise ValueError(f"--benchmark-dir 缺少 DSM.tif 或 Orthomosaic_pix_surf_group0.tif：{cand}")
    return None


def _bind_job_memory(fn):
    """跑管线期间让分块并行读到这次任务的内存预算。"""
    from functools import wraps

    from ms_mosaic.memory import set_job_memory_gb

    @wraps(fn)
    def wrapper(*args, **kwargs):
        set_job_memory_gb(kwargs.get("memory_gb"))
        try:
            return fn(*args, **kwargs)
        finally:
            set_job_memory_gb(None)

    return wrapper


@_bind_job_memory
def run_mosaic(
    input_dir: Path,
    out_dir: Path,
    *,
    max_frames: int | None = None,
    max_index: int | None = None,
    dsm_gsd: float | None = None,
    workers: int | None = None,
    workers_at: int | None = None,
    workers_dense: int | None = None,
    workers_ortho: int | None = None,
    memory_gb: float | None = None,
    bands: Sequence[str] | None = None,
    cache_dir: Path | None = None,
    reuse_dsm: Path | None = None,
    benchmark_dir: Path | None = None,
    match_reference_color: bool = False,
    reporter: ProgressReporter | None = None,
    control: ControlState | None = None,
    start_stage: str | None = None,
    stop_after_stage: str | None = None,
    min_agl_m: float | None = None,
    max_tilt_deg: float | None = None,
    drop_white_panel: bool = True,
    require_pos: bool = True,
    sigma_xy_m: float | None = None,
    sigma_z_m: float | None = None,
    sigma_attitude_deg: float | None = None,
    outlier_threshold_px: float | None = None,
    calibrate_intrinsics: bool = True,
    n_layers: int | None = None,
    z_margin_m: float | None = None,
    spike_tolerance_m: float | None = None,
    max_fill_gap_m: float | None = None,
    color_correction: str | None = None,
    seamline_enabled: bool = True,
    write_pdf_report: bool = True,
    write_json_report: bool = True,
    products_dir_name: str | None = None,
    process_dir: Path | str | None = None,
    grid_reference: Path | str | None = None,
    terrain_margin_lo_m: float | None = None,
    terrain_margin_hi_m: float | None = None,
    terrain_min_half_span_m: float | None = None,
    radiometric_normalize: bool = False,
    edge_trim_m: float | None = None,
    flatten_edge_win_m: float | None = None,
    flatten_edge_band_m: float | None = None,
) -> dict[str, Any]:
    """完整交付：空三 → DSM → 真正射 → 拼接线/融合 → 正射成果 + PDF 报告。

    出图只看输入航摄：GSD 由航高/焦距估计，覆盖取相片足迹。
    benchmark_dir 只写比对报告，不改格网、覆盖、颜色。
    match_reference_color 是调试套色，合格主路径不要开。
    """
    t0 = time.time()
    reporter = reporter or NullReporter()
    control = control or ControlState()
    start_stage = start_stage or "S0_io"
    input_dir, out_dir = _assert_out_outside_input(Path(input_dir), Path(out_dir))
    products_dir = out_dir / (products_dir_name or PRODUCTS_DIRNAME)
    extras_dir = Path(process_dir) if process_dir else out_dir / EXTRAS_DIRNAME
    at_workers = workers_at if workers_at is not None else (workers or 10)
    dense_workers = workers_dense if workers_dense is not None else workers
    ortho_workers = workers_ortho if workers_ortho is not None else workers
    fill_gap = MAX_FILL_GAP_M if max_fill_gap_m is None else float(max_fill_gap_m)
    rgb_gain = (color_correction or "off_for_ms").strip().lower() not in ("off", "false", "0", "none")
    products_dir.mkdir(parents=True, exist_ok=True)
    extras_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir) if cache_dir is not None else out_dir / "cache" / "features"

    wanted = tuple(bands) if bands is not None else BAND_ORDER
    timings: dict[str, float] = {}

    # 当前阶段。日志行随事件写入数据库，刷屏的块进度不发。
    held_stage = {"id": None}

    def log(msg: str) -> None:
        with _log_lock:
            print(msg, flush=True)
        stage_id = held_stage["id"]
        if not stage_id or ("/" in msg and "块" in msg and "进程" not in msg):
            return
        try:
            reporter.event("stage_log", stage_id=stage_id, message=msg)
        except Exception:
            pass

    def enter(stage_id: str, message: str = "") -> None:
        held_stage["id"] = stage_id
        reporter.stage_start(stage_id, message)

    from ms_mosaic.checkpoint import checkpoint_path, resolve_sparse, should_reuse_at

    reuse_at = should_reuse_at(start_stage, checkpoint_path(cache_dir))
    # 编目节点在进入空三前结束。影像筛选发生在空三内部，这里只记交接耗时。
    if stage_index(start_stage) <= stage_index("S1_catalog"):
        reporter.stage_done("S1_catalog", max(0.0, time.time() - t0))
    enter("S2_at", "复用空三检查点" if reuse_at else "空三 / 稀疏重建")
    t = time.time()
    sparse = resolve_sparse(
        input_dir,
        cache_dir,
        start_stage=start_stage,
        max_index=max_index,
        max_frames=max_frames,
        workers=at_workers,
        memory_gb=memory_gb,
        min_agl_m=min_agl_m,
        max_tilt_deg=max_tilt_deg,
        drop_white_panel=drop_white_panel,
        require_pos=require_pos,
        sigma_xy_m=sigma_xy_m,
        sigma_z_m=sigma_z_m,
        sigma_attitude_deg=sigma_attitude_deg,
        outlier_threshold_px=outlier_threshold_px,
        calibrate_intrinsics=calibrate_intrinsics,
        log=log,
    )
    timings["at"] = 0.0 if reuse_at else time.time() - t
    reporter.progress("S2_at", 100, global_percent_for("S2_at", 100), "空三完成")
    reporter.stage_done("S2_at", timings["at"])
    if not reuse_at:
        early = _barrier(control, reporter, "S2_at")
        if early:
            return early
        early = _stop_here(stop_after_stage, "S2_at", {"timings": timings, "n_shots": sparse.counts.get("n_usable")}, reporter)
        if early:
            return early
    block = sparse.block
    at = sparse.at
    # 空三可含全部波段影像；密集/足迹仍只用主波段 Color，避免同曝光多镜头重复撑大范围
    primary_ids = {im.index for im in block.primary()}
    dense_poses = {i: p for i, p in sparse.poses.items() if i in primary_ids}
    dense_paths = {i: sparse.image_paths[i] for i in dense_poses if i in sparse.image_paths}
    if not dense_poses:
        dense_poses = dict(sparse.poses)
        dense_paths = dict(sparse.image_paths)

    t = time.time()
    pts = sparse.points
    # 参考面取覆盖区按面积加权的稳健地形面：稀疏点密度在纹理强的地方高出几十倍，
    # 直接取中位数会被那一小块区域拉走，估出的航高偏大、GSD 偏粗。
    ground_z = ground_reference_z(pts) if len(pts) else block.ground_z
    cams = {i: sparse.camera for i in dense_poses}
    ref_dir = resolve_benchmark_dir(benchmark_dir, required=benchmark_dir is not None)
    if match_reference_color and ref_dir is None:
        raise ValueError("--match-reference-color 需要同时指定 --benchmark-dir")
    if radiometric_normalize and ref_dir is None:
        raise ValueError("辐射归一化需要同时指定 benchmark_dir 作为档位参考")
    lock = resolve_grid_reference(grid_reference)
    ortho_grid = None
    if lock is not None:
        lock_dsm, lock_ortho = lock
        grid = Grid.from_raster(lock_dsm)
        dsm_gsd = grid.gsd
        if lock_ortho is not None:
            ortho_grid = Grid.from_raster(lock_ortho)
        log(
            f"锁定交付格网：GSD={grid.gsd:.15f} 宽高={grid.width}×{grid.height} "
            f"正射={'锁定' if ortho_grid is not None else '按 DSM 一半'}"
        )
    else:
        if dsm_gsd is None:
            dsm_gsd = estimate_dsm_gsd(sparse.camera, dense_poses, ground_z)
            log(f"DSM GSD 由航高/焦距估计为 {dsm_gsd:.6f} m（正射为其一半）")
        grid = grid_from_footprints(cams, dense_poses, ground_z, dsm_gsd, block.crs)
    coverage = smooth_coverage_mask(
        coverage_from_footprints(grid, cams, dense_poses, ground_z), grid.gsd
    )
    trim = 0.0 if edge_trim_m is None else float(edge_trim_m)
    if trim > 0:
        coverage = erode_coverage(coverage, grid.gsd, trim)
        log(f"交付覆盖从足迹外缘往里收 {trim:.1f} m（裁掉单视斜视边）")
    if ref_dir is not None:
        log(f"验收参考仅用于比对：{ref_dir}")
    field = None
    if reuse_dsm is not None:
        import rasterio

        enter("S3_dense", f"复用 DSM {reuse_dsm}")
        src = Path(reuse_dsm).expanduser().resolve()
        with rasterio.open(src) as ds:
            z = ds.read(1).astype(np.float64)
            if ds.nodata is not None:
                z = np.where(z == ds.nodata, np.nan, z)
            grid = Grid(ds.transform, ds.width, ds.height, str(ds.crs))
        coverage = smooth_coverage_mask(
            coverage_from_footprints(grid, cams, dense_poses, ground_z), grid.gsd
        )
        if trim > 0:
            coverage = erode_coverage(coverage, grid.gsd, trim)
        z = complete_dsm_coverage(
            z,
            grid,
            coverage,
            max_fill_gap_m=fill_gap,
            clip_to_coverage=True,
            ref_z=pts[:, 2],
            margin_lo_m=terrain_margin_lo_m,
            margin_hi_m=terrain_margin_hi_m,
            min_half_span_m=terrain_min_half_span_m,
        )
        valid = np.isfinite(z)
        dsm = Dsm(
            grid,
            z.astype(np.float32),
            {
                "gsd": grid.gsd,
                "width": grid.width,
                "height": grid.height,
                "n_valid": int(valid.sum()),
                "fill_ratio": float(valid.mean()),
                "z_min": float(np.nanmin(z)) if valid.any() else float("nan"),
                "z_median": float(np.nanmedian(z)) if valid.any() else float("nan"),
                "z_max": float(np.nanmax(z)) if valid.any() else float("nan"),
                "reused": str(src),
            },
        )
        timings["dense"] = 0.0
        log(f"复用 DSM {src}，足迹补洞后有效 {dsm.stats['fill_ratio']:.3f}")
        reporter.stage_done("S3_dense", 0.0)
    else:
        enter("S3_dense", "密集匹配")
        z_margin = float(z_margin_m) if z_margin_m is not None else estimate_z_margin_m(pts)
        if z_margin_m is None:
            log(f"高程搜索半宽 {z_margin:.1f} m（由稀疏点起伏估计）")
        else:
            log(f"高程搜索半宽 {z_margin:.1f} m（方案指定）")
        dense_cfg = DenseConfig(workers=dense_workers, z_margin_m=z_margin)
        if n_layers is not None:
            dense_cfg.n_layers = int(n_layers)
        if max_tilt_deg is not None:
            dense_cfg.max_tilt_deg = float(max_tilt_deg)
        field = compute_height_field(
            grid,
            pts,
            {i: sparse.camera for i in dense_poses},
            dense_poses,
            dense_paths,
            cfg=dense_cfg,
            log=log,
            workers=dense_workers,
        )
        timings["dense"] = time.time() - t
        reporter.stage_done("S3_dense", timings["dense"])
        early = _barrier(control, reporter, "S3_dense")
        if early:
            return early
        early = _stop_here(stop_after_stage, "S3_dense", {"timings": timings}, reporter)
        if early:
            return early
        dsm_kw: dict[str, Any] = {
            "coverage": coverage,
            "max_fill_gap_m": fill_gap,
            "clip_to_coverage": True,
            # 合理高程带必须用空三点定，不能用 DSM 自身的中值 ± MAD：本测区
            # 后者给出上限 1806 m，而地形最高 1830 m，山顶被整片裁成空洞再从
            # 低处补回来，最高一成地面因此偏低 23 m。空三点 p99+50 给出 1848 m。
            "ref_z": pts[:, 2],
            "margin_lo_m": terrain_margin_lo_m,
            "margin_hi_m": terrain_margin_hi_m,
            "min_half_span_m": terrain_min_half_span_m,
        }
        if spike_tolerance_m is not None:
            dsm_kw["tolerance_m"] = float(spike_tolerance_m)
        dsm = build_dsm(field, **dsm_kw)

    t = time.time()
    enter("S4_dsm", "写出 DSM")
    dsm_path = write_geotiff(dsm, products_dir / "DSM.tif")
    timings["dsm"] = time.time() - t
    reporter.stage_done("S4_dsm", timings["dsm"])
    early = _barrier(control, reporter, "S4_dsm")
    if early:
        early.update({"files": {"dsm": str(dsm_path)}, "timings": timings})
        return early
    early = _stop_here(
        stop_after_stage,
        "S4_dsm",
        {"files": {"dsm": str(dsm_path)}, "timings": timings, "n_shots": sparse.counts.get("n_usable")},
        reporter,
    )
    if early:
        return early

    if stage_index(start_stage) > stage_index("S5_ortho"):
        pass

    if ortho_grid is None:
        ortho_grid = dsm.grid.refine(ORTHO_OVER_DSM)
    enter("S5_ortho", f"正射波段 {wanted}")
    files: dict[str, Any] = {"bands": {}, "dsm": str(dsm_path), "mosaic_dir": str(products_dir)}
    labels_rgb = None
    views_rgb: list[int] = []
    t_ortho = time.time()
    # 贴边采样面只跟 DSM 有关，各波段相同。算一次，避免八个波段各滤一遍同一张高程。
    ortho_z = flatten_edge_z(
        dsm.z,
        dsm.grid.gsd,
        win_m=40.0 if flatten_edge_win_m is None else float(flatten_edge_win_m),
        band_m=80.0 if flatten_edge_band_m is None else float(flatten_edge_band_m),
    )

    def _run_band(band: str, band_workers: int | None) -> tuple[str, np.ndarray, np.ndarray, list[int]] | None:
        cameras, poses, paths = transfer_band(block, at.cameras, at.poses, band)
        if not poses:
            log(f"跳过 {band}：没有对应的空三外方位")
            return None
        log(f"=== 正射 {group_name(band)} ({band})  {len(poses)} 张 ===")
        mosaic, labels, views = render_band(
            ortho_grid,
            ortho_z,
            dsm.grid,
            cameras,
            poses,
            paths,
            band=band,
            workers=band_workers,
            gain=rgb_gain if band == RGB_BAND else False,
            log=log,
        )
        if band == RGB_BAND and ref_dir is not None and (radiometric_normalize or match_reference_color):
            # 全局仿射辐射归一化：只改档位，不动纹理与局部对比。自研输出忠实于
            # 源 JPG（G 均值 114.3，源 107.4），商业是减掉暗电平后的档位
            # （G 91.1，饱和 0）。实测校正后分位残差 0.9–1.7 DN、饱和率 0%。
            ref_rgb = ref_dir / "Orthomosaic_pix_surf_group0.tif"
            mosaic = scale_mosaic_to_reference(mosaic, ortho_grid, ref_rgb)
            if match_reference_color:
                # 低频替换会把参考的低频底搬过来，属调试手段，不进交付主路径
                mosaic = match_lowfreq_to_reference(mosaic, ortho_grid, ref_rgb)
        return band, mosaic, labels, views

    def _commit_band(band: str, mosaic: np.ndarray, labels: np.ndarray, views: list[int]) -> None:
        out_path = products_dir / group_name(band)
        # 足迹已按 edge_trim_m 收过，这里只清 RGB 全 0，不再二次腐蚀
        write_band_product(mosaic, ortho_grid, band, out_path, trim_m=0)
        files["bands"][band] = str(out_path)
        log(f"已写出 {out_path.name}（{band} 完成）")
        if band == RGB_BAND or band == PRIMARY_BAND:
            files["rgb"] = str(out_path)
            nonlocal labels_rgb, views_rgb
            labels_rgb, views_rgb = labels, views

    rgb_bands = [b for b in wanted if b == RGB_BAND]
    ms_bands = [b for b in wanted if b != RGB_BAND]
    tile_n = resolve_workers(ortho_workers)
    for band in rgb_bands:
        got = _run_band(band, ortho_workers)
        if got:
            _commit_band(*got)
    n_par, per = split_ortho_pools(len(ms_bands), tile_n)
    if len(ms_bands) > 1 and n_par > 1:
        log(f"多光谱正射并发 {n_par} 波段 × {per} 进程（块进程合计 ≤ {tile_n}）")
        with ThreadPoolExecutor(max_workers=n_par) as pool:
            futs = [pool.submit(_run_band, band, per) for band in ms_bands]
            for fut in as_completed(futs):
                got = fut.result()
                if got:
                    _commit_band(*got)
    else:
        for band in ms_bands:
            got = _run_band(band, ortho_workers)
            if got:
                _commit_band(*got)
    timings["ortho"] = time.time() - t_ortho
    reporter.stage_done("S5_ortho", timings["ortho"])
    early = _barrier(control, reporter, "S5_ortho")
    if early:
        early["files"] = files
        return early
    early = _stop_here(
        stop_after_stage,
        "S5_ortho",
        {"files": files, "timings": timings, "n_shots": sparse.counts.get("n_usable")},
        reporter,
    )
    if early:
        return early

    t_report = time.time()
    enter("S6_report", "质量报告")
    pc_path = write_pseudocolor(dsm.z, dsm.grid, extras_dir / "DSM_pseudocolor.tif")
    kml_path = write_kml(ortho_grid, extras_dir / "area.kml", image=Path(files.get("rgb", "DSM.tif")).name)
    files["pseudocolor"] = str(pc_path)
    files["kml"] = str(kml_path)
    if seamline_enabled and labels_rgb is not None:
        seam_path = seamline_geojson(labels_rgb, ortho_grid, views_rgb, extras_dir / "seamlines.geojson")
        files["seamlines"] = str(seam_path)

    n_shots = sparse.counts.get("n_usable", len(block.primary()))
    # 复用检查点时 keypoint_counts 可能为空：从特征缓存补齐，保证全波段统计
    kp_counts = dict(sparse.keypoint_counts or {})
    if len(kp_counts) < len(sparse.image_paths):
        from ms_mosaic.features import load_keypoints

        for idx, path in sparse.image_paths.items():
            if idx in kp_counts:
                continue
            try:
                kp_counts[idx] = len(load_keypoints(Path(path), cache_dir=cache_dir))
            except Exception:
                kp_counts[idx] = 0
    reg_ids = registered_image_ids(at, sparse.tracks)
    cam_info = cameras_table(
        block,
        at,
        at.stats.get("rms_reprojection_px"),
        registered=reg_ids,
        rms_by_band=band_rms_px(block, at),
    )
    payload = build_quality_payload(
        input_dir=input_dir,
        block=block,
        at=at,
        tracks=sparse.tracks,
        matches=sparse.matches,
        keypoint_counts=kp_counts,
        dsm=dsm,
        dense_stats=field.stats if field is not None else {"reused_dsm": True, **{k: dsm.stats[k] for k in ("n_valid", "fill_ratio") if k in dsm.stats}},
        files=files,
        timings=timings,
        elapsed_s=time.time() - t0,
        n_scanned=sparse.counts.get("n_shots", 0),
        n_usable=n_shots,
        cameras_info=cam_info,
    )
    primary = [im for im in block.primary() if im.index in at.poses]
    primary_cam = at.cameras.get(PRIMARY_BAND) or next(iter(at.cameras.values()), None)
    color_cams = {im.index: primary_cam for im in primary} if primary_cam is not None else {}
    color_poses = {im.index: at.poses[im.index] for im in primary}
    payload["figures"] = render_report_figures(
        fig_dir=out_dir / "report" / "figures",
        files=files,
        gps_rows=payload.get("gps_rows") or [],
        cameras=color_cams,
        poses=color_poses,
        points=at.points,
        ground_z=float(dsm.stats.get("z_median", block.ground_z)),
        grid=dsm.grid,
    )
    payload["method"] = "AT + object-space MVS + true ortho + graph-cut seam + Laplacian blend"
    if write_json_report:
        paths = write_report(out_dir, payload)
        files["report_json"] = paths["report_json"]
        files["report_md"] = paths["report_md"]
    if write_pdf_report:
        pdf_path = write_quality_pdf(payload, out_dir / REPORT_PDF_NAME)
        files["report_pdf"] = str(pdf_path)
    payload["files"] = files
    if ref_dir is not None:
        from ms_mosaic.compare import write_delivery_report

        log("=== 与验收参考逐像元比对 ===")

        cmp_path = out_dir / "比对报告.md"
        try:
            crep = write_delivery_report(products_dir, ref_dir, cmp_path)
            files["compare"] = str(cmp_path)
            log(cmp_path.read_text(encoding="utf-8"))
            if not crep.ok:
                log("比对未达标，见 比对报告.md")
        except Exception as exc:
            log(f"比对失败（成果已写出）：{exc}")
    reporter.stage_done("S6_report", time.time() - t_report)
    payload["status"] = "succeeded"
    payload["completed_stage"] = "S6_report"
    return payload
