"""CLI 入口：旗标名与 Web 处理方案 param key 一一对应（snake_case → --kebab-case）。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ms_mosaic.local_defaults import INPUT as LOCAL_INPUT
from ms_mosaic.local_defaults import available as local_input_ready

# 与 backend param_catalog / param_definitions 对齐（不含仅服务端拼路径的派生项）
_PATH_KEYS = (
    "input_dir",
    "output_dir",
    "cache_dir",
    "log_dir",
    "process_dir",
    "benchmark_dir",
    "reuse_dsm",
    "grid_reference",
)
_FLOAT_KEYS = (
    "memory_gb",
    "min_agl_m",
    "max_tilt_deg",
    "sigma_xy_m",
    "sigma_z_m",
    "sigma_attitude_deg",
    "outlier_threshold_px",
    "dsm_gsd",
    "z_margin_m",
    "spike_tolerance_m",
    "max_fill_gap_m",
    "terrain_margin_lo_m",
    "terrain_margin_hi_m",
    "terrain_min_half_span_m",
    "edge_trim_m",
    "flatten_edge_win_m",
    "flatten_edge_band_m",
)
_INT_KEYS = (
    "cpus",
    "max_index",
    "max_frames",
    "workers_at",
    "workers_dense",
    "workers_ortho",
    "n_layers",
)
_BOOL_KEYS = (
    "match_reference_color",
    "drop_white_panel",
    "require_pos",
    "calibrate_intrinsics",
    "seamline_enabled",
    "write_pdf_report",
    "write_json_report",
    "radiometric_normalize",
)
_STR_KEYS = (
    "products_dir_name",
    "run_mode",
    "start_stage",
    "stop_after_stage",
    "preset",
    "primary_band",
    "color_correction",
)


def _flag(name: str) -> str:
    """param key → CLI 旗标，如 input_dir → --input-dir。"""
    return "--" + name.replace("_", "-")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="多光谱/RGB 航摄目录 → 正射大图 + 质量报告。"
        "命令行参数名与 Web「处理方案」一致（如 --input-dir 对应 input_dir）。"
    )
    parser.add_argument(
        _flag("input_dir"),
        required=not local_input_ready,
        default=LOCAL_INPUT if local_input_ready else None,
        type=Path,
        help="输入测区目录（Web: input_dir）。本机有默认测区时可省略",
    )
    parser.add_argument(
        _flag("output_dir"),
        required=True,
        type=Path,
        help="输出目录（Web: output_dir）",
    )
    parser.add_argument(_flag("cache_dir"), type=Path, default=None, help="特征缓存目录（Web: cache_dir）")
    parser.add_argument(_flag("log_dir"), type=Path, default=None, help="日志目录（Web: log_dir）")
    parser.add_argument(_flag("process_dir"), type=Path, default=None, help="中间过程目录（Web: process_dir）")
    parser.add_argument(
        _flag("products_dir_name"),
        default=None,
        help="成果子目录名（Web: products_dir_name），默认 拼图结果",
    )
    parser.add_argument(
        _flag("run_mode"),
        choices=("full", "until_stage", "step"),
        default=None,
        help="运行模式（Web: run_mode）",
    )
    parser.add_argument(_flag("start_stage"), default=None, help="起始阶段（Web: start_stage）")
    parser.add_argument(_flag("stop_after_stage"), default=None, help="停止阶段（Web: stop_after_stage）")
    parser.add_argument(
        _flag("preset"),
        choices=("rgb_preview",),
        default=None,
        help="快捷预设（Web: preset）。rgb_preview 强制 bands=Color",
    )
    parser.add_argument(_flag("benchmark_dir"), type=Path, default=None, help="比对参考目录（Web: benchmark_dir）")
    parser.add_argument(_flag("memory_gb"), type=float, default=None, help="任务内存预算 GB（Web: memory_gb）")
    parser.add_argument(_flag("cpus"), type=int, default=None, help="任务 CPU 核数（Web: cpus）")

    parser.add_argument(_flag("max_index"), type=int, default=None, help="最大影像编号（Web: max_index）")
    parser.add_argument(_flag("max_frames"), type=int, default=None, help="最大曝光数（Web: max_frames）")
    parser.add_argument(_flag("min_agl_m"), type=float, default=None, help="最低航高米（Web: min_agl_m）")
    parser.add_argument(_flag("max_tilt_deg"), type=float, default=None, help="最大倾角度（Web: max_tilt_deg）")

    parser.add_argument(
        _flag("primary_band"),
        default=None,
        help="主波段（Web: primary_band）。当前管线固定 Color，非 Color 将报错",
    )
    parser.add_argument(_flag("workers_at"), type=int, default=None, help="空三并行度（Web: workers_at）")
    parser.add_argument(_flag("sigma_xy_m"), type=float, default=None, help="GNSS 平面先验米（Web: sigma_xy_m）")
    parser.add_argument(_flag("sigma_z_m"), type=float, default=None, help="GNSS 高程先验米（Web: sigma_z_m）")
    parser.add_argument(
        _flag("sigma_attitude_deg"),
        type=float,
        default=None,
        help="IMU 姿态先验度（Web: sigma_attitude_deg）",
    )
    parser.add_argument(
        _flag("outlier_threshold_px"),
        type=float,
        default=None,
        help="外点阈值像素（Web: outlier_threshold_px）",
    )

    parser.add_argument(_flag("dsm_gsd"), type=float, default=None, help="DSM 分辨率米（Web: dsm_gsd）")
    parser.add_argument(_flag("reuse_dsm"), type=Path, default=None, help="复用 DSM 路径（Web: reuse_dsm）")
    parser.add_argument(_flag("n_layers"), type=int, default=None, help="高程扫描层数（Web: n_layers）")
    parser.add_argument(_flag("z_margin_m"), type=float, default=None, help="高程搜索半宽米（Web: z_margin_m）")
    parser.add_argument(_flag("workers_dense"), type=int, default=None, help="密集匹配进程数（Web: workers_dense）")
    parser.add_argument(
        _flag("terrain_margin_lo_m"),
        type=float,
        default=None,
        help="地形带下余量米（Web: terrain_margin_lo_m）",
    )
    parser.add_argument(
        _flag("terrain_margin_hi_m"),
        type=float,
        default=None,
        help="地形带上余量米（Web: terrain_margin_hi_m）",
    )
    parser.add_argument(
        _flag("terrain_min_half_span_m"),
        type=float,
        default=None,
        help="无空三半宽下限米（Web: terrain_min_half_span_m）",
    )

    parser.add_argument(
        _flag("spike_tolerance_m"),
        type=float,
        default=None,
        help="尖刺剔除阈值米（Web: spike_tolerance_m）",
    )
    parser.add_argument(
        _flag("max_fill_gap_m"),
        type=float,
        default=None,
        help="空洞填充跨度米（Web: max_fill_gap_m）",
    )

    parser.add_argument(
        _flag("bands"),
        default=None,
        help="正射波段，逗号分隔（Web: bands）。例: Color 或 Color,550nm",
    )
    parser.add_argument(
        _flag("color_correction"),
        default=None,
        help="颜色校正（Web: color_correction），如 off_for_ms",
    )
    parser.add_argument(_flag("workers_ortho"), type=int, default=None, help="正射并行度（Web: workers_ortho）")
    parser.add_argument(
        _flag("grid_reference"),
        type=Path,
        default=None,
        help="锁定交付格网（Web: grid_reference）",
    )
    parser.add_argument(_flag("edge_trim_m"), type=float, default=None, help="边缘收边米（Web: edge_trim_m）")
    parser.add_argument(
        _flag("flatten_edge_win_m"),
        type=float,
        default=None,
        help="贴边纠正窗口米（Web: flatten_edge_win_m）",
    )
    parser.add_argument(
        _flag("flatten_edge_band_m"),
        type=float,
        default=None,
        help="贴边纠正带宽米（Web: flatten_edge_band_m）",
    )

    for key in _BOOL_KEYS:
        parser.add_argument(
            _flag(key),
            action=argparse.BooleanOptionalAction,
            default=None,
            help=f"布尔参数（Web: {key}）。--{key.replace('_', '-')} / --no-{key.replace('_', '-')}",
        )

    return parser


def args_to_params(args: argparse.Namespace) -> dict[str, Any]:
    """把已解析命名空间收成与 Web params_snapshot 同结构的字典。"""
    params: dict[str, Any] = {}
    for key in _PATH_KEYS:
        val = getattr(args, key, None)
        if val is not None:
            params[key] = str(val)
    for key in _FLOAT_KEYS + _INT_KEYS + _STR_KEYS:
        val = getattr(args, key, None)
        if val is not None and val != "":
            params[key] = val
    for key in _BOOL_KEYS:
        val = getattr(args, key, None)
        if val is not None:
            params[key] = bool(val)

    bands_raw = getattr(args, "bands", None)
    if bands_raw:
        params["bands"] = [s.strip() for s in str(bands_raw).split(",") if s.strip()]

    if params.get("preset") == "rgb_preview":
        params["bands"] = ["Color"]

    primary = params.get("primary_band")
    if primary is not None and str(primary) != "Color":
        raise SystemExit(f"primary_band 当前仅支持 Color，收到: {primary!r}")

    # 必填路径始终写入，便于下游
    params["input_dir"] = str(args.input_dir)
    params["output_dir"] = str(args.output_dir)
    return params


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    params = args_to_params(args)

    from ms_mosaic.stage_runner import run_stages

    result = run_stages(Path(params["input_dir"]), Path(params["output_dir"]), params=params)
    status = result.get("status", "succeeded")
    print(f"status={status}")
    if result.get("completed_stage"):
        print(f"completed_stage={result['completed_stage']}")
    if status == "failed":
        print(f"error={result.get('error')}")
        return 2
    if status == "awaiting_continue":
        print("阶段完成，可用相同 --output-dir 并设置 --start-stage 为下一阶段继续")
        return 10
    if status == "cancelled":
        return 130
    files = result.get("files") or {}
    print(f"shots={result.get('n_shots')}  elapsed={result.get('elapsed_s')}s  crs={result.get('crs')}")
    print(f"mosaic={files.get('mosaic_dir')}")
    print(f"rgb={files.get('rgb')}")
    print(f"dsm={files.get('dsm')}")
    print(f"report={files.get('report_pdf') or files.get('report_json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
