"""按阶段编排 run_mosaic，支持起止阶段与 awaiting_continue。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ms_mosaic.control import ControlState
from ms_mosaic.cpu import resolve_cpu_budget, set_job_cpus
from ms_mosaic.pipeline import run_mosaic
from ms_mosaic.progress import NullReporter, ProgressReporter, global_percent_for, stage_index


def _should_run(stage: str, start: str, stop: str | None) -> bool:
    i = stage_index(stage)
    if i < stage_index(start):
        return False
    if stop is not None and i > stage_index(stop):
        return False
    return True


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_path(value: Any) -> Path | None:
    if not value:
        return None
    return Path(str(value))


def run_stages(
    input_dir: Path,
    out_dir: Path,
    *,
    params: dict[str, Any] | None = None,
    reporter: ProgressReporter | None = None,
    control: ControlState | None = None,
) -> dict[str, Any]:
    """执行管线。

    返回 dict 含 status: succeeded | awaiting_continue | failed | cancelled
    以及 pipeline 原有字段（若已跑完相关阶段）。
    """
    params = dict(params or {})
    reporter = reporter or NullReporter()
    control = control or ControlState()
    set_job_cpus(resolve_cpu_budget(params.get("cpus")))
    try:
        return _run_stages_body(input_dir, out_dir, params=params, reporter=reporter, control=control)
    finally:
        set_job_cpus(None)


def _run_stages_body(
    input_dir: Path,
    out_dir: Path,
    *,
    params: dict[str, Any],
    reporter: ProgressReporter,
    control: ControlState,
) -> dict[str, Any]:
    start = params.get("start_stage") or "S0_io"
    run_mode = params.get("run_mode") or "full"
    stop = params.get("stop_after_stage")
    if run_mode == "step":
        stop = start
        params["stop_after_stage"] = stop
        run_mode = "until_stage"
    if run_mode == "full":
        stop = "S6_report"

    bands = params.get("bands")
    if isinstance(bands, str):
        bands = tuple(s.strip() for s in bands.split(",") if s.strip())
    elif isinstance(bands, list):
        bands = tuple(bands)

    if _should_run("S0_io", start, stop):
        t0 = time.time()
        reporter.stage_start("S0_io", "校验输入输出路径")
        reporter.progress("S0_io", 50, global_percent_for("S0_io", 50), "检查路径")
        inp = Path(params.get("input_dir") or input_dir).expanduser().resolve()
        out = Path(params.get("output_dir") or out_dir).expanduser().resolve()
        if out == inp or inp in out.parents:
            reporter.event("failed", error="输出目录不能落在输入目录内", stage_id="S0_io")
            return {"status": "failed", "error": "输出目录不能落在输入目录内"}
        out.mkdir(parents=True, exist_ok=True)
        (out / "log").mkdir(parents=True, exist_ok=True)
        barrier = control.checkpoint_barrier()
        if barrier == "cancel":
            reporter.event("cancelled", stage_id="S0_io")
            return {"status": "cancelled"}
        reporter.stage_done("S0_io", time.time() - t0)
        if stop == "S0_io":
            reporter.event("awaiting_continue", stage_id="S0_io", message="S0 完成，等待继续")
            return {"status": "awaiting_continue", "completed_stage": "S0_io"}

    t0 = time.time()
    try:
        reporter.stage_start(start if start != "S0_io" else "S1_catalog", "进入主计算")
        result = run_mosaic(
            Path(params.get("input_dir") or input_dir),
            Path(params.get("output_dir") or out_dir),
            max_frames=_as_int(params.get("max_frames")),
            max_index=_as_int(params.get("max_index")),
            dsm_gsd=_as_float(params.get("dsm_gsd")),
            workers=_as_int(params.get("workers")),
            workers_at=_as_int(params.get("workers_at")),
            workers_dense=_as_int(params.get("workers_dense")),
            workers_ortho=_as_int(params.get("workers_ortho")),
            memory_gb=_as_float(params.get("memory_gb")),
            bands=bands,
            cache_dir=_as_path(params.get("cache_dir")),
            reuse_dsm=_as_path(params.get("reuse_dsm")),
            benchmark_dir=_as_path(params.get("benchmark_dir")),
            match_reference_color=_as_bool(params.get("match_reference_color"), False),
            reporter=reporter,
            control=control,
            start_stage=start,
            stop_after_stage=stop if run_mode == "until_stage" else None,
            min_agl_m=_as_float(params.get("min_agl_m")),
            max_tilt_deg=_as_float(params.get("max_tilt_deg")),
            drop_white_panel=_as_bool(params.get("drop_white_panel"), True),
            require_pos=_as_bool(params.get("require_pos"), True),
            sigma_xy_m=_as_float(params.get("sigma_xy_m")),
            sigma_z_m=_as_float(params.get("sigma_z_m")),
            sigma_attitude_deg=_as_float(params.get("sigma_attitude_deg")),
            outlier_threshold_px=_as_float(params.get("outlier_threshold_px")),
            calibrate_intrinsics=_as_bool(params.get("calibrate_intrinsics"), True),
            n_layers=_as_int(params.get("n_layers")),
            z_margin_m=_as_float(params.get("z_margin_m")),
            spike_tolerance_m=_as_float(params.get("spike_tolerance_m")),
            max_fill_gap_m=_as_float(params.get("max_fill_gap_m")),
            color_correction=params.get("color_correction") or "off_for_ms",
            seamline_enabled=_as_bool(params.get("seamline_enabled"), True),
            write_pdf_report=_as_bool(params.get("write_pdf_report"), True),
            write_json_report=_as_bool(params.get("write_json_report"), True),
            products_dir_name=params.get("products_dir_name") or None,
            process_dir=_as_path(params.get("process_dir")),
            grid_reference=_as_path(params.get("grid_reference")),
            terrain_margin_lo_m=_as_float(params.get("terrain_margin_lo_m")),
            terrain_margin_hi_m=_as_float(params.get("terrain_margin_hi_m")),
            terrain_min_half_span_m=_as_float(params.get("terrain_min_half_span_m")),
            radiometric_normalize=_as_bool(params.get("radiometric_normalize"), False),
            edge_trim_m=_as_float(params.get("edge_trim_m")),
            flatten_edge_win_m=_as_float(params.get("flatten_edge_win_m")),
            flatten_edge_band_m=_as_float(params.get("flatten_edge_band_m")),
        )
    except Exception as exc:  # noqa: BLE001
        reporter.event("failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}

    status = result.get("status") or "succeeded"
    if status == "awaiting_continue":
        reporter.event(
            "awaiting_continue",
            stage_id=result.get("completed_stage"),
            message=result.get("message") or "阶段完成，等待继续",
        )
    elif status == "cancelled":
        reporter.event("cancelled")
    elif status == "succeeded":
        reporter.event("succeeded", message="全流程完成", n_shots=result.get("n_shots"))
    result.setdefault("elapsed_s", time.time() - t0)
    return result
