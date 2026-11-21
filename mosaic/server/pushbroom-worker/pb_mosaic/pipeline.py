"""一个推扫任务的完整流程：核对 → 轨迹 → 航带 → 配准 → 镶嵌 → 成果 → 报告。

命令行和队列子进程共用。进度、日志通过 Reporter 回报；cancelled() 返回真时在下一个检查点停下。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from pb_mosaic import __version__
from pb_mosaic.geom.calibration import CalibrationCheck, check_calibration
from pb_mosaic.geom.footprint import StripGeometry, strip_geometry
from pb_mosaic.geom.grid import OutputGrid, default_gsd, grid_from_bounds
from pb_mosaic.geom.sensor import Sensor, SensorError, sensor_from, sensor_sources
from pb_mosaic.geom.strips import split_strips
from pb_mosaic.geom.trajectory import Trajectory, build_trajectory
from pb_mosaic.ingest.flight import Flight, FlightError, check_flight
from pb_mosaic.license_check import LicenseError, enforce_license_or_raise
from pb_mosaic.mosaic.align import AlignParams, AlignResult, align_strips
from pb_mosaic.mosaic.merge import Cancelled, write_mosaic
from pb_mosaic.mosaic.refine import RefineParams, RefineResult, refine_strips
from pb_mosaic.mosaic.tiepoints import TieParams, TieResult, bend_strips
from pb_mosaic.products.envi_out import write_mosaic_header
from pb_mosaic.products.footprints import write_footprints, write_trajectory_csv
from pb_mosaic.products.preview import RgbSink, rgb_positions, stretch_to_png, write_display_rgb
from pb_mosaic.products.report import write_report
from pb_mosaic.stages import LABELS, global_percent

log = logging.getLogger("pb_mosaic.pipeline")

DEFAULT_MAX_MEMORY_GB = 8.0
# 镶嵌可用的内存占总预算的比例，其余留给解释器、配准中间数组和页缓存
MOSAIC_MEMORY_SHARE = 0.6
# 写成果前要求的额外剩余空间
DISK_MARGIN_BYTES = 1 << 30
# 进度消息最短间隔
PROGRESS_INTERVAL_S = 1.0

SENSOR_KEYS = ("gsd_cross_m", "flight_height_m", "sample_sign", "along_offset_m", "boresight_px")


class PipelineError(Exception):
    """可以直接给用户看的失败原因。"""


class Reporter(Protocol):
    def progress(self, stage: str, fraction: float, message: str) -> None: ...

    def stage_done(self, stage: str, elapsed_s: float, message: str) -> None: ...

    def log(self, message: str, level: str = "info") -> None: ...


class LogReporter:
    """只写日志，命令行和测试用。"""

    def progress(self, stage: str, fraction: float, message: str) -> None:
        log.info("[%s %5.1f%%] %s", LABELS.get(stage, stage), global_percent(stage, fraction), message)

    def stage_done(self, stage: str, elapsed_s: float, message: str) -> None:
        log.info("[%s] 完成，%.1f 秒。%s", LABELS.get(stage, stage), elapsed_s, message)

    def log(self, message: str, level: str = "info") -> None:
        log.log(logging.WARNING if level == "warning" else logging.ERROR if level == "error" else logging.INFO, message)


@dataclass
class JobParams:
    gsd_m: float | None = None
    bands: list[int] | None = None  # 1 起始
    align: bool = True
    refine: bool = True
    tiepoints: bool = True
    sensor: dict[str, Any] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "JobParams":
        raw = dict(raw or {})
        p = cls()
        if raw.get("gsd_m") is not None:
            gsd = float(raw.pop("gsd_m"))
            if not 0.005 <= gsd <= 2.0:
                raise PipelineError(f"成果分辨率 {gsd} m 超出 0.005–2 m")
            p.gsd_m = gsd
        else:
            raw.pop("gsd_m", None)
        bands = raw.pop("bands", None)
        if bands is not None:
            if not isinstance(bands, list) or not bands or not all(isinstance(b, int) and b >= 1 for b in bands):
                raise PipelineError("波段列表必须是从 1 开始的整数")
            if len(set(bands)) != len(bands):
                raise PipelineError("波段序号有重复")
            p.bands = sorted(bands)
        for key in ("align", "refine", "tiepoints"):
            if key in raw:
                val = raw.pop(key)
                if not isinstance(val, bool):
                    raise PipelineError(f"{key} 只能是 true 或 false")
                setattr(p, key, val)
        for key in SENSOR_KEYS:
            if raw.get(key) is not None:
                p.sensor[key] = raw.pop(key)
        p.ignored = sorted(raw)
        return p

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "gsd_m": self.gsd_m,
            "bands": self.bands,
            "align": self.align,
            "refine": self.refine,
            "tiepoints": self.tiepoints,
        }
        out.update(self.sensor)
        return out


@dataclass
class RunResult:
    products: list[dict[str, Any]]
    report: dict[str, Any]


class _Stage:
    """计时 + 节流的进度回报。"""

    def __init__(self, reporter: Reporter, timings: dict[str, float], stage: str) -> None:
        self.reporter = reporter
        self.timings = timings
        self.stage = stage
        self.t0 = time.monotonic()
        self.last = 0.0

    def progress(self, fraction: float, message: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last >= PROGRESS_INTERVAL_S:
            self.last = now
            self.reporter.progress(self.stage, fraction, message)

    def done(self, message: str = "") -> None:
        elapsed = time.monotonic() - self.t0
        self.timings[self.stage] = round(elapsed, 2)
        self.reporter.stage_done(self.stage, elapsed, message)


def _safe_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", name).strip("._") or "flight"


def max_memory_bytes() -> int:
    try:
        gb = float(os.environ.get("PB_MAX_MEMORY_GB") or DEFAULT_MAX_MEMORY_GB)
    except ValueError:
        gb = DEFAULT_MAX_MEMORY_GB
    return int(max(1.0, gb) * (1 << 30))


def _band_index(flight: Flight, bands: list[int] | None) -> np.ndarray | None:
    if bands is None:
        return None
    bad = [b for b in bands if b > flight.hdr.bands]
    if bad:
        raise PipelineError(f"波段序号超出范围（共 {flight.hdr.bands} 个波段）：{bad[:10]}")
    return np.asarray([b - 1 for b in bands], dtype=np.int64)


def _align_band(flight: Flight) -> int:
    """配准用的波段（0 起始）：默认波段里的绿，没有就取中间。"""
    db = flight.hdr.default_bands
    if len(db) >= 2:
        return db[1] - 1
    return flight.hdr.bands // 2


def _check_disk(out_dir: Path, need: int) -> None:
    free = shutil.disk_usage(out_dir).free
    if free < need + DISK_MARGIN_BYTES:
        raise PipelineError(
            f"输出盘剩余 {free / (1 << 30):.1f} GiB，成果约需 {need / (1 << 30):.1f} GiB（另留 1 GiB 余量），请清理后重试"
        )


def _product(path: Path, root: Path, kind: str, label: str) -> dict[str, Any]:
    return {"path": str(path.relative_to(root)), "kind": kind, "label": label, "bytes": path.stat().st_size}


_SOURCE_LABELS = {"task": "任务参数", "deployment": "部署配置 PB_SENSOR_*", "builtin": "FS-60C 内置标定", "header": "头文件"}


def describe_sources(sources: dict[str, str]) -> str:
    groups: dict[str, list[str]] = {}
    for key, src in sources.items():
        groups.setdefault(src, []).append(key)
    return "；".join(f"{_SOURCE_LABELS[s]}：{', '.join(groups[s])}" for s in _SOURCE_LABELS if s in groups)


# 配准阶段内：整条平移、精配准、特征点沿航向弯曲
RIGID_SHARE = 0.4
TIE_START = 0.85


@dataclass
class Geometry:
    """配准之后的航带几何：平移（整条 + 精配准）和逐条剪切都已加上。"""

    geoms: list[StripGeometry]
    alignment: AlignResult
    refine: RefineResult
    tie: TieResult
    offsets: dict[int, tuple[float, float]]
    calibration: CalibrationCheck

    @property
    def note(self) -> str:
        parts = [self.alignment.note]
        if self.refine.applied and self.refine.note:
            parts.append(self.refine.note)
        if self.tie.note and self.tie.applied:
            parts.append(self.tie.note)
        return "；".join(p for p in parts if p)

    def alignment_dict(self) -> dict[str, Any]:
        d = self.alignment.as_dict()
        d["rigid_offsets_east_north_m"] = d.pop("offsets_east_north_m")
        d["offsets_east_north_m"] = {str(k): [round(v[0], 3), round(v[1], 3)] for k, v in self.offsets.items()}
        d["refine"] = self.refine.as_dict()
        d["tiepoints"] = self.tie.as_dict()
        return d


def solve_geometry(
    flight: Flight,
    traj: Trajectory,
    strips: list,
    sensor: Sensor,
    params: JobParams,
    st: _Stage,
    cancelled: Callable[[], bool] | None,
) -> Geometry:
    geoms = [strip_geometry(traj, s, sensor) for s in strips]
    cube = flight.open_cube()
    ab = _align_band(flight)
    st.progress(0.0, f"用第 {ab + 1} 波段做航带间配准", force=True)

    def read_band(l0: int, l1: int) -> np.ndarray:
        return np.asarray(cube[l0:l1, ab, :])

    alignment = align_strips(
        geoms,
        read_band,
        traj.epsg,
        AlignParams(enabled=params.align),
        cancelled=cancelled,
        progress=lambda f, m: st.progress(RIGID_SHARE * f, m, force=True),
    )
    geoms = [g.translated(*alignment.offsets[g.strip.index]) for g in geoms]
    enabled = params.align and params.refine
    if enabled and len(geoms) > 1 and not any(p.used for p in alignment.pairs):
        refine = RefineResult(
            corrections={g.strip.index: (0.0, 0.0, 0.0) for g in geoms},
            note="整条平移没有可靠的航带重叠，未做精配准",
        )
    else:
        refine = refine_strips(
            geoms,
            read_band,
            traj.epsg,
            RefineParams(enabled=enabled),
            cancelled=cancelled,
            progress=lambda f, m: st.progress(RIGID_SHARE + (TIE_START - RIGID_SHARE) * f, m, force=True),
        )
    offsets = dict(alignment.offsets)
    if refine.applied:
        geoms = [g.corrected(*refine.corrections[g.strip.index]) for g in geoms]
        offsets = {
            s: (v[0] + refine.corrections[s][0], v[1] + refine.corrections[s][1]) for s, v in alignment.offsets.items()
        }
    linked = {s for p in alignment.pairs if p.used for s in (p.a, p.b)}
    calibration = check_calibration(geoms, offsets, sensor, linked)
    tie_on = params.align and params.tiepoints and len(geoms) > 1
    tie = bend_strips(
        geoms,
        read_band,
        traj.epsg,
        TieParams(enabled=tie_on),
        cancelled=cancelled,
        progress=lambda f, m: st.progress(TIE_START + (1.0 - TIE_START) * f, m, force=True),
    )
    if tie.applied:
        geoms = [g.bent(*tie.lines[g.strip.index]) for g in geoms]
    return Geometry(geoms, alignment, refine, tie, offsets, calibration)


def _require_license() -> None:
    """客户镜像里流水线入口自己验许可：绕过 worker 走命令行或直接调用，同样受限。"""
    try:
        enforce_license_or_raise()
    except LicenseError as exc:
        raise PipelineError(str(exc)) from exc


def calibrate_flight(input_dir: Path, raw_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """只做到配准，给出本架次的安装参数自检。命令行 calibrate 用。"""
    _require_license()
    params = JobParams.from_dict(raw_params)
    flight = check_flight(input_dir)
    traj = build_trajectory(flight.gps)
    strips = split_strips(traj)
    if not strips:
        raise PipelineError("没有切出可用航带")
    sensor = sensor_from(flight.hdr, params.sensor)
    geo = solve_geometry(flight, traj, strips, sensor, params, _Stage(LogReporter(), {}, "P3_align"), None)
    return {
        "flight": flight.name,
        "strips": len(strips),
        "sensor": sensor.as_dict(),
        "sensor_source": describe_sources(sensor_sources(params.sensor)),
        "alignment": geo.note,
        "refine": geo.refine.as_dict(),
        "calibration": geo.calibration,
    }


def run(
    input_dir: Path,
    output_dir: Path,
    params: dict[str, Any] | None = None,
    *,
    reporter: Reporter | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RunResult:
    _require_license()
    reporter = reporter or LogReporter()
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    for sub in ("products", "process", "log", "report"):
        (output_dir / sub).mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "worker_version": __version__,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "status": "failed",
        "warnings": [],
        "stage_timings": {},
    }
    timings: dict[str, float] = report["stage_timings"]

    def check_cancel() -> None:
        if cancelled and cancelled():
            raise Cancelled()

    try:
        # 成功时 _run 自己写报告，成果清单里报告的字节数才对得上
        return _run(input_dir, output_dir, params, reporter, check_cancel, cancelled, report, timings)
    except Cancelled:
        report["status"] = "cancelled"
        report["error"] = "用户取消"
        raise
    except (PipelineError, FlightError, SensorError) as exc:
        report["error"] = str(exc)
        raise PipelineError(str(exc)) from exc
    except Exception as exc:
        report["error"] = f"内部错误：{type(exc).__name__}: {exc}"
        raise
    finally:
        if report["status"] != "succeeded":
            report["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                write_report(output_dir / "report", report)
            except Exception:  # noqa: BLE001
                log.exception("write report failed")


def _run(
    input_dir: Path,
    output_dir: Path,
    raw_params: dict[str, Any] | None,
    reporter: Reporter,
    check_cancel: Callable[[], None],
    cancelled: Callable[[], bool] | None,
    report: dict[str, Any],
    timings: dict[str, float],
) -> RunResult:
    params = JobParams.from_dict(raw_params)
    report["params"] = params.as_dict()
    if params.ignored:
        msg = f"忽略不认识的参数：{', '.join(params.ignored)}"
        report["warnings"].append(msg)
        reporter.log(msg, "warning")

    st = _Stage(reporter, timings, "P0_check")
    st.progress(0.0, "核对 hdr / spe / gps", force=True)
    flight = check_flight(input_dir)
    report["flight"] = flight.summary()
    for w in flight.warnings:
        report["warnings"].append(w)
        reporter.log(w, "warning")
    band_idx = _band_index(flight, params.bands)
    st.done(f"{flight.hdr.samples} 列 × {flight.hdr.lines} 行 × {flight.hdr.bands} 波段，定位 {len(flight.gps)} 条")
    check_cancel()

    st = _Stage(reporter, timings, "P1_trajectory")
    traj = build_trajectory(flight.gps)
    report["line_clock"] = traj.clock.as_dict()
    if traj.clock.method != "linear":
        report["warnings"].append(traj.clock.note)
        reporter.log(traj.clock.note, "warning")
    write_trajectory_csv(output_dir / "process" / "trajectory.csv", traj)
    st.done(traj.clock.note)
    check_cancel()

    st = _Stage(reporter, timings, "P2_strips")
    strips = split_strips(traj)
    if not strips:
        raise PipelineError("没有切出可用航带：航迹全程在转弯、悬停，或直线段都短于 30 米")
    sensor = sensor_from(flight.hdr, params.sensor)
    report["sensor"] = sensor.as_dict()
    report["sensor_source"] = describe_sources(sensor_sources(params.sensor))
    st.done(f"{len(strips)} 条航带，共 {sum(s.lines for s in strips)} 行")
    check_cancel()

    st = _Stage(reporter, timings, "P3_align")
    geo = solve_geometry(flight, traj, strips, sensor, params, st, cancelled)
    geoms = geo.geoms
    report["alignment"] = geo.alignment_dict()
    report["calibration"] = geo.calibration.as_dict()
    if geo.refine.note.startswith("精配准未采用"):
        report["warnings"].append(geo.refine.note)
        reporter.log(geo.refine.note, "warning")
    if geo.calibration.warning:
        report["warnings"].append(geo.calibration.warning)
        reporter.log(geo.calibration.warning, "warning")
    st.done(geo.note)
    check_cancel()

    st = _Stage(reporter, timings, "P4_mosaic")
    cube = flight.open_cube()
    grid = _make_grid(geoms, sensor.gsd_cross_m, traj, params.gsd_m)
    report["grid"] = grid.as_dict()
    nb = flight.hdr.bands if band_idx is None else int(band_idx.size)
    cube_bytes = grid.width * grid.height * nb * flight.hdr.dtype.itemsize
    _check_disk(output_dir, cube_bytes + grid.width * grid.height * 6)
    stem = _safe_name(flight.name)
    prod = output_dir / "products"
    spe = prod / f"{stem}_mosaic.spe"
    hdr_path = prod / f"{stem}_mosaic.hdr"
    tif = prod / f"{stem}_rgb.tif"
    display_tif = prod / f"{stem}_rgb_display.tif"
    png = prod / f"{stem}_preview.png"
    positions = rgb_positions(flight.hdr, band_idx)
    sink = RgbSink(tif, grid, positions)
    st.progress(
        0.0,
        f"格网 {grid.width} × {grid.height}，{grid.gsd * 100:.2f} cm，{nb} 波段，约 {cube_bytes / (1 << 30):.1f} GiB",
        force=True,
    )
    try:
        stats = write_mosaic(
            cube,
            geoms,
            grid,
            spe,
            band_idx=band_idx,
            budget_bytes=int(max_memory_bytes() * MOSAIC_MEMORY_SHARE),
            on_block=sink,
            progress=lambda f, m: st.progress(f, m),
            cancelled=cancelled,
        )
    except BaseException:
        sink.abort()
        raise
    if stats.valid_pixels == 0:
        sink.abort()
        raise PipelineError("成果里没有有效像元，请检查定位与传感器参数")
    report["coverage"] = {
        "valid_pixels": stats.valid_pixels,
        "area_m2": round(stats.valid_pixels * grid.gsd * grid.gsd, 1),
        "valid_ratio": round(stats.valid_pixels / float(grid.width * grid.height), 4),
        "source_lines_read": stats.source_lines_read,
    }
    st.done(f"有效覆盖 {report['coverage']['area_m2']:.0f} m²")
    check_cancel()

    st = _Stage(reporter, timings, "P5_products")
    st.progress(0.0, "写头文件和 RGB GeoTIFF 金字塔", force=True)
    write_mosaic_header(
        hdr_path,
        flight.hdr,
        grid,
        band_idx,
        description=f"pb_mosaic {__version__} pushbroom mosaic of {flight.name}",
    )
    sink.finish()
    preview = stretch_to_png(sink.thumb, png)
    report["preview"] = preview
    write_display_rgb(tif, display_tif, preview["stretch_dn"])
    footprints = prod / f"{stem}_footprints.geojson"
    write_footprints(footprints, traj, geoms, geo.offsets, stats.strip_pixels)
    strips_out = []
    for g in geoms:
        d = g.strip.as_dict()
        d["align_offset_east_north_m"] = [round(v, 3) for v in geo.offsets[g.strip.index]]
        d["shear_deg"] = round(float(np.degrees(g.shear)), 3)
        d["mosaic_pixels"] = int(stats.strip_pixels.get(g.strip.index, 0))
        strips_out.append(d)
    report["strips"] = strips_out
    products = [
        _product(spe, output_dir, "cube", "成果立方体（ENVI BIL，原始 DN）"),
        _product(hdr_path, output_dir, "header", "成果头文件"),
        _product(tif, output_dir, "rgb", "RGB GeoTIFF（原始 DN）"),
        _product(display_tif, output_dir, "rgb_display", "RGB GeoTIFF（显示拉伸）"),
        _product(png, output_dir, "preview", "预览图"),
        _product(footprints, output_dir, "footprints", "航带足迹"),
    ]
    report["products"] = products
    st.done(f"{len(products)} 个成果文件")

    st = _Stage(reporter, timings, "P6_report")
    report["status"] = "succeeded"
    report["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_report(output_dir / "report", report)
    st.done("report.json / report.md")
    result_products = products + [
        _product(output_dir / "report" / "report.json", output_dir, "report", "处理报告（JSON）"),
        _product(output_dir / "report" / "report.md", output_dir, "report", "处理报告"),
    ]
    return RunResult(products=result_products, report=report)


def _make_grid(geoms: list[StripGeometry], gsd_cross: float, traj, gsd: float | None) -> OutputGrid:
    steps = np.concatenate([g.step for g in geoms])
    along = float(np.median(steps)) if steps.size else gsd_cross
    res = gsd or default_gsd(gsd_cross, along)
    b = np.array([g.bounds() for g in geoms])
    grid = grid_from_bounds(traj.epsg, b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max(), res)
    if grid.width * grid.height > 4_000_000_000:
        raise PipelineError(
            f"成果格网 {grid.width} × {grid.height} 过大，请把分辨率调粗（当前 {res * 100:.2f} cm）"
        )
    return grid
