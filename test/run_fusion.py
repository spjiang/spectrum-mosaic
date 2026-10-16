#!/usr/bin/env python3
"""RGB + 8 通道融合：经算法 HTTP API 从头跑到 #19。

输入全部是文件（不要目录）。当前流水线两景一组：
  --rgb         可见光 3 通道 .tif …
  --ms          8 通道 DN .tif …
  --pos         轨迹 pos.csv
  --timestamps  三路时间戳 timestamps.json
  --dem         数字高程 dem.tif
  --calib       定标/相机 calib.json
  --dark        可选暗电流帧

  .venv/bin/python run_fusion.py --bench \\
    --rgb rgb_0001.tif rgb_0002.tif \\
    --ms  ms_0001.tif ms_0002.tif \\
    --pos pos.csv --timestamps timestamps.json \\
    --dem dem.tif --calib calib.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import warnings

import certifi
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import xy as transform_xy
from rasterio.windows import Window
from rasterio.warp import transform as warp_transform

ROOT = Path(__file__).resolve().parent
DOC_DIR = ROOT.parent
SOURCE = ROOT.parents[2] / "source"
sys.path.insert(0, str(SOURCE))

from common.io import save_geotiff  # noqa: E402  仅生成起始 DEM
from common.rs.photogrammetry import gsd_m, meters_per_deg  # noqa: E402  仅生成 POS 间隔

log = logging.getLogger("fusion")

API = os.environ.get("FUSION_API", "http://127.0.0.1:28800").rstrip("/")
RGB_FILES: list[Path] = []
MS_FILES: list[Path] = []
DARK_FILE: Path | None = None
POS_CSV: Path = ROOT / "input" / "pos" / "pos.csv"
TIMESTAMPS: Path = ROOT / "input" / "pos" / "timestamps.json"
DEM_FILE: Path = ROOT / "input" / "pos" / "dem.tif"
CALIB_FILE: Path = ROOT / "input" / "pos" / "calib.json"
WORK = ROOT / "work"
OUT = ROOT / "out"
LOG_FMT = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
TIMINGS: list[dict] = []
SCALE_INFO: dict = {}

FRAME_H, FRAME_W = 240, 320
BENCH_H, BENCH_W = 2048, 2048
BENCH_OVERLAP = 0.30
TARGET_KM2 = 12.0
TARGET_GSD_M = 0.3
S2_GSD_M = 10.0
S2_BASE = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/Q/GF/2026/2/S2B_49QGF_20260202_0_L2A"
S2_WINDOW = (5200, 1800)  # row, col on 10980 tile；连续实景，不铺贴
S2_BANDS = [
    ("Blue", 490, "B02.tif"),
    ("Green", 560, "B03.tif"),
    ("Red", 665, "B04.tif"),
    ("RedEdge", 705, "B05.tif"),
    ("RedEdge2", 740, "B06.tif"),
    ("RedEdge3", 783, "B07.tif"),
    ("NIR", 842, "B08.tif"),
    ("NIRn", 865, "B8A.tif"),
]
ALT_M = 120.0
FOCAL_MM = 8.0
PIXEL_UM = 5.5
GAIN = 0.01
OFFSET = 0.0
PANEL_RHO = 0.6
RGB_SHIFT_PX = 2  # 人为错开，让 #19 能算出位移
BAND_NAMES = [
    "Blue 490nm",
    "Green 560nm",
    "Red 665nm",
    "RedEdge 705nm",
    "RedEdge2 740nm",
    "RedEdge3 783nm",
    "NIR 842nm",
    "NIRn 865nm",
]


def _setup_log() -> None:
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    stream = logging.StreamHandler()
    stream.setFormatter(LOG_FMT)
    log.addHandler(stream)
    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="common.rs.photogrammetry")


def _attach_run_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(LOG_FMT)
    log.addHandler(handler)


def step(title: str):
    class _Step:
        def __init__(self, name: str):
            self.name = name
            self.t0 = 0.0

        def __enter__(self):
            log.info("")
            log.info("========== %s ==========", self.name)
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            dt = time.perf_counter() - self.t0
            if exc_type is None:
                log.info("完成  %.2fs", dt)
            else:
                log.error("失败  %.2fs  %s", dt, exc)
            return False

    return _Step(title)


def _write_raw_tif(path: Path, hwc: np.ndarray, descriptions: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bands = np.moveaxis(hwc, -1, 0)
    profile = {
        "driver": "GTiff",
        "height": int(bands.shape[1]),
        "width": int(bands.shape[2]),
        "count": int(bands.shape[0]),
        "dtype": bands.dtype.name,
        "compress": "lzw",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(bands)
        if descriptions:
            for i, name in enumerate(descriptions, start=1):
                dst.set_band_description(i, name)


def _window_center_lonlat(src, row0: int, col0: int, h: int, w: int) -> tuple[float, float]:
    x, y = transform_xy(src.transform, row0 + (h - 1) / 2.0, col0 + (w - 1) / 2.0)
    lon, lat = warp_transform(src.crs, CRS.from_epsg(4326), [x], [y])
    return float(lon[0]), float(lat[0])


def prepare_start_files(*, force: bool = False) -> None:
    """从上级真实 Sentinel-2 裁两景重叠条带，写成指定文件。"""
    if len(RGB_FILES) != 2 or len(MS_FILES) != 2:
        raise SystemExit("起始数据需要恰好两景 --rgb 与两景 --ms")
    needed = [*RGB_FILES, *MS_FILES, POS_CSV, TIMESTAMPS, DEM_FILE, CALIB_FILE]
    if all(p.exists() for p in needed) and not force:
        log.info("起始数据已存在，跳过生成（需要重做加 --force）")
        log.info("  RGB  %s", ", ".join(p.name for p in RGB_FILES))
        log.info("  8通道 %s", ", ".join(p.name for p in MS_FILES))
        log.info("  POS  %s", POS_CSV.name)
        return

    rgb_src = DOC_DIR / "rgb_aligned.tif"
    hsi_src = DOC_DIR / "hsi_ref.tif"
    if not rgb_src.exists() or not hsi_src.exists():
        raise FileNotFoundError(f"缺少上级真实影像：{rgb_src.name} / {hsi_src.name}")

    from rasterio.windows import Window

    for p in needed + ([DARK_FILE] if DARK_FILE else []):
        p.parent.mkdir(parents=True, exist_ok=True)

    windows = [
        (80, 80),
        (80, 220),
    ]
    log.info("从真实场景裁切起始帧：%s  %s", rgb_src.name, hsi_src.name)

    rgb_frames = []
    ms_frames = []
    centers = []
    with rasterio.open(rgb_src) as rgb, rasterio.open(hsi_src) as hsi:
        lon0, lat0 = _window_center_lonlat(rgb, windows[0][0], windows[0][1], FRAME_H, FRAME_W)
        gsd = gsd_m(ALT_M, PIXEL_UM, FOCAL_MM)
        m_lon, m_lat = meters_per_deg(lat0)
        for i, (row0, col0) in enumerate(windows, start=1):
            win = Window(col0, row0, FRAME_W, FRAME_H)
            rgb_arr = np.moveaxis(rgb.read(window=win), 0, -1)
            hsi_arr = np.moveaxis(hsi.read(window=win), 0, -1)
            rgb_arr = np.clip(rgb_arr.astype(np.float32) * 0.82, 0, 248).astype(np.uint8)
            if i == 1:
                rgb_arr = np.roll(rgb_arr, RGB_SHIFT_PX, axis=1)
            dn = np.clip(hsi_arr.astype(np.float64) * 28000.0 + 1800.0, 0, 65535).astype(np.uint16)
            rgb_path = RGB_FILES[i - 1]
            ms_path = MS_FILES[i - 1]
            _write_raw_tif(rgb_path, rgb_arr, ["Red", "Green", "Blue"])
            _write_raw_tif(ms_path, dn, BAND_NAMES)
            dcol = col0 - windows[0][1]
            drow = row0 - windows[0][0]
            lon = lon0 + dcol * gsd / m_lon
            lat = lat0 - drow * gsd / m_lat
            ts = (i - 1) * 1.6
            rgb_frames.append({"id": rgb_path.stem, "ts": ts + 0.03, "file": rgb_path.name})
            ms_frames.append({"id": ms_path.stem, "ts": ts, "file": ms_path.name})
            centers.append((ts, lon, lat))
            log.info(
                "  帧 %s  RGB=%s  8通道=%s  中心=%.5fE, %.5fN  相对位移 %d px",
                i,
                rgb_path.name,
                ms_path.name,
                lon,
                lat,
                dcol,
            )

        if DARK_FILE is not None:
            dark = np.full((FRAME_H, FRAME_W, hsi.count), 80, dtype=np.uint16)
            _write_raw_tif(DARK_FILE, dark, BAND_NAMES)

    # 10 Hz POS，覆盖两帧曝光，带轻微姿态噪声
    t0, t1 = centers[0][0], centers[-1][0]
    times = np.linspace(t0 - 0.4, t1 + 0.4, 33)
    lon0, lat0 = centers[0][1], centers[0][2]
    lon1, lat1 = centers[-1][1], centers[-1][2]
    rng = np.random.default_rng(19)
    pos_rows = []
    csv_lines = ["time,lat,lon,alt,roll,pitch,yaw"]
    for t in times:
        w = 0.0 if t1 == t0 else np.clip((t - t0) / (t1 - t0), 0.0, 1.0)
        lat = lat0 + w * (lat1 - lat0) + float(rng.normal(0, 1.5e-6))
        lon = lon0 + w * (lon1 - lon0) + float(rng.normal(0, 1.5e-6))
        alt = ALT_M + float(rng.normal(0, 0.4))
        roll = float(rng.normal(0.15, 0.04))
        pitch = float(rng.normal(-0.12, 0.04))
        yaw = float(rng.normal(0.0, 0.3))
        pos_rows.append(
            {"ts": float(t), "lat": lat, "lon": lon, "alt": alt, "roll": roll, "pitch": pitch, "yaw": yaw}
        )
        csv_lines.append(f"{t:.4f},{lat:.8f},{lon:.8f},{alt:.3f},{roll:.4f},{pitch:.4f},{yaw:.4f}")
    POS_CSV.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    stamps = {"hsi_frames": ms_frames, "rgb_frames": rgb_frames, "pos": pos_rows}
    TIMESTAMPS.write_text(json.dumps(stamps, indent=2, ensure_ascii=False), encoding="utf-8")

    yy, xx = np.mgrid[0:64, 0:64]
    dem = (12.0 + 6.0 * (xx / 63.0) + 3.0 * np.sin(yy / 8.0)).astype(np.float32)
    save_geotiff(dem, DEM_FILE)

    calib = {
        "gain": GAIN,
        "offset": OFFSET,
        "panel_reflectance": PANEL_RHO,
        "alt_m": ALT_M,
        "focal_mm": FOCAL_MM,
        "pixel_um": PIXEL_UM,
        "lever_e_m": 0.12,
        "lever_n_m": -0.08,
        "lever_u_m": 0.05,
        "bit_depth": 16,
        "note": "演示定标；gain/offset 不是真实相机实验室系数",
    }
    CALIB_FILE.write_text(json.dumps(calib, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("已写入 RGB=%s  MS=%s  POS=%s", [p.name for p in RGB_FILES], [p.name for p in MS_FILES], POS_CSV.name)


def _s2_env() -> rasterio.Env:
    return rasterio.Env(
        **{
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "AWS_NO_SIGN_REQUEST": "YES",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
            "CURL_CA_BUNDLE": certifi.where(),
            "SSL_CERT_FILE": certifi.where(),
        }
    )


def prepare_bench_files(*, force: bool = False) -> None:
    """从 Sentinel-2 L2A 原分辨率裁两景真实相邻重叠条带（不铺贴、不合成地物）。"""
    if len(RGB_FILES) != 2 or len(MS_FILES) != 2:
        raise SystemExit("--bench 需要恰好两景 --rgb 与两景 --ms")
    if CALIB_FILE.exists() and not force:
        prev = json.loads(CALIB_FILE.read_text(encoding="utf-8"))
        if prev.get("profile") == "bench12_s2" and all(p.exists() for p in [*RGB_FILES, *MS_FILES, POS_CSV, TIMESTAMPS, DEM_FILE]):
            log.info("真实 Sentinel-2 条带已存在，跳过生成（需要重做加 --force）")
            log.info("  帧 %sx%s  传感器GSD=%sm", prev.get("frame_h"), prev.get("frame_w"), prev.get("gsd_m"))
            return

    for p in [*RGB_FILES, *MS_FILES, POS_CSV, TIMESTAMPS, DEM_FILE, CALIB_FILE]:
        p.parent.mkdir(parents=True, exist_ok=True)
    if DARK_FILE is not None and DARK_FILE.exists():
        DARK_FILE.unlink()

    shift = int(round(BENCH_W * (1.0 - BENCH_OVERLAP)))
    canvas_w = BENCH_W + shift
    row0, col0 = S2_WINDOW
    log.info("下载 Copernicus Sentinel-2 L2A 真景：S2B_49QGF_20260202  窗 %d,%d  %d×%d  GSD=10 m", row0, col0, BENCH_H, canvas_w)
    log.info("  8 通道 = B02/B03/B04/B05/B06/B07/B08/B8A；RGB = 官方真彩色 TCI。无铺贴。")

    tci_url = f"{S2_BASE}/TCI.tif"
    with _s2_env():
        with rasterio.open(tci_url) as src:
            if row0 + BENCH_H > src.height or col0 + canvas_w > src.width:
                raise RuntimeError(f"窗口超出 S2 瓦片 {src.width}×{src.height}")
            win = Window(col0, row0, canvas_w, BENCH_H)
            rgb_canvas = np.moveaxis(src.read(window=win), 0, -1)
            transform = src.window_transform(win)
            crs = src.crs
        ms_bands = []
        names = []
        for name, wl, fname in S2_BANDS:
            with rasterio.open(f"{S2_BASE}/{fname}") as src:
                band = src.read(
                    1,
                    window=Window(col0, row0, canvas_w, BENCH_H),
                    out_shape=(BENCH_H, canvas_w),
                    resampling=rasterio.enums.Resampling.bilinear,
                )
                ms_bands.append(band)
                names.append(f"{name} {wl}nm")
                log.info("  已读 %s %dnm", name, wl)
    dn_canvas = np.stack(ms_bands, axis=-1).astype(np.uint16)

    rgb_frames = []
    ms_frames = []
    centers = []
    windows = [(0, 0), (0, shift)]
    for i, (r, c) in enumerate(windows, start=1):
        rgb_arr = rgb_canvas[r : r + BENCH_H, c : c + BENCH_W]
        dn = dn_canvas[r : r + BENCH_H, c : c + BENCH_W]
        rgb_path = RGB_FILES[i - 1]
        ms_path = MS_FILES[i - 1]
        _write_raw_tif(rgb_path, rgb_arr.astype(np.uint8), ["Red", "Green", "Blue"])
        _write_raw_tif(ms_path, dn, names)
        rr, cc = r + (BENCH_H - 1) / 2.0, c + (BENCH_W - 1) / 2.0
        x, y = transform_xy(transform, rr, cc)
        lon, lat = warp_transform(crs, CRS.from_epsg(4326), [x], [y])
        lon, lat = float(lon[0]), float(lat[0])
        ts = (i - 1) * 2.4
        rgb_frames.append({"id": rgb_path.stem, "ts": ts + 0.03, "file": rgb_path.name})
        ms_frames.append({"id": ms_path.stem, "ts": ts, "file": ms_path.name})
        centers.append((ts, lon, lat))
        ground_km2 = BENCH_H * BENCH_W * S2_GSD_M * S2_GSD_M / 1e6
        log.info("  帧 %s  中心=%.5fE, %.5fN  真地面约 %.1f km²（10 m 像元）", i, lon, lat, ground_km2)

    t0, t1 = centers[0][0], centers[-1][0]
    times = np.linspace(t0 - 0.4, t1 + 0.4, 41)
    lon_a, lat_a = centers[0][1], centers[0][2]
    lon_b, lat_b = centers[-1][1], centers[-1][2]
    alt_m = 120.0
    rng = np.random.default_rng(19)
    pos_rows = []
    csv_lines = ["time,lat,lon,alt,roll,pitch,yaw"]
    for t in times:
        w = 0.0 if t1 == t0 else np.clip((t - t0) / (t1 - t0), 0.0, 1.0)
        lat = lat_a + w * (lat_b - lat_a)
        lon = lon_a + w * (lon_b - lon_a)
        pos_rows.append(
            {
                "ts": float(t),
                "lat": lat,
                "lon": lon,
                "alt": alt_m,
                "roll": float(rng.normal(0.15, 0.04)),
                "pitch": float(rng.normal(-0.12, 0.04)),
                "yaw": float(rng.normal(0.0, 0.3)),
            }
        )
        p = pos_rows[-1]
        csv_lines.append(f"{t:.4f},{p['lat']:.8f},{p['lon']:.8f},{p['alt']:.3f},{p['roll']:.4f},{p['pitch']:.4f},{p['yaw']:.4f}")
    POS_CSV.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    TIMESTAMPS.write_text(
        json.dumps({"hsi_frames": ms_frames, "rgb_frames": rgb_frames, "pos": pos_rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    save_geotiff(np.full((256, 256), 8.0, dtype=np.float32), DEM_FILE)
    mosaic_px = BENCH_H * (BENCH_W + shift)
    uav_equiv_km2 = mosaic_px * TARGET_GSD_M * TARGET_GSD_M / 1e6
    target_px = TARGET_KM2 * 1e6 / (TARGET_GSD_M ** 2)
    calib = {
        "profile": "bench12_s2",
        "source": "Copernicus Sentinel-2 L2A S2B_49QGF_20260202_0_L2A",
        "source_note": "官方 10 m 真彩色 + 8 个 MSI 波段连续裁切，非铺贴、非几何色块。仓库无中达瑞和航飞时用此业界公开实景。",
        "gain": GAIN,
        "offset": OFFSET,
        "panel_reflectance": PANEL_RHO,
        "alt_m": alt_m,
        "focal_mm": FOCAL_MM,
        "pixel_um": PIXEL_UM,
        "gsd_m": S2_GSD_M,
        "target_km2": TARGET_KM2,
        "target_gsd_m": TARGET_GSD_M,
        "frame_h": BENCH_H,
        "frame_w": BENCH_W,
        "overlap": BENCH_OVERLAP,
        "tile_rows": 256,
        "tile_size": 1024,
        "lever_e_m": 0.12,
        "lever_n_m": -0.08,
        "lever_u_m": 0.05,
        "bit_depth": 16,
        "note": f"像元 {mosaic_px} ≈ UAV {TARGET_GSD_M} m 下 {uav_equiv_km2:.3f} km²；外推 {TARGET_KM2} km² 倍数 {target_px / mosaic_px:.1f}",
    }
    CALIB_FILE.write_text(json.dumps(calib, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("真地面（10 m）镶嵌约 %.1f km²；按像元等价 UAV 0.3 m 约 %.3f km²，外推倍数 ×%.1f", mosaic_px * 100 / 1e6, uav_equiv_km2, target_px / mosaic_px)
    log.info("已写入 RGB=%s  MS=%s", [str(p) for p in RGB_FILES], [str(p) for p in MS_FILES])


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _ping_api() -> None:
    try:
        raw = subprocess.check_output(
            ["curl", "-sS", "--noproxy", "*", "--max-time", "3", f"{API}/health"],
            text=True,
        )
        body = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"算法服务连不上 {API}（{exc}）。请先启动：algorithm/source/scripts/start.sh"
        ) from exc
    if body.get("status") != "ok":
        raise SystemExit(f"算法服务异常：{body}")
    log.info("API  %s  已实现 %s/%s", API, body.get("implemented"), body.get("algorithms"))


def api_run(
    algo_id: str,
    file: Path,
    file2: Path | None = None,
    params: dict | None = None,
) -> dict:
    """POST /api/v1/{id}/run，返回 JSON；墙钟计入 TIMINGS。"""
    http_dir = WORK / "_http"
    http_dir.mkdir(parents=True, exist_ok=True)
    tmp = http_dir / f"{algo_id}_{time.time_ns()}.json"
    cmd = [
        "curl",
        "-sS",
        "--noproxy",
        "*",
        "--max-time",
        "3600",
        "-o",
        str(tmp),
        "-w",
        "%{http_code} %{time_total}",
        "-X",
        "POST",
        f"{API}/api/v1/{algo_id}/run",
        "-F",
        f"file=@{file}",
    ]
    if file2 is not None:
        cmd += ["-F", f"file2=@{file2}"]
    cmd += ["-F", f"params={json.dumps(params or {}, ensure_ascii=False)}"]
    extra = f" + file2={file2.name}" if file2 is not None else ""
    log.info("  POST /api/v1/%s/run  file=%s%s", algo_id, file.name, extra)
    t0 = time.perf_counter()
    try:
        trailer = subprocess.check_output(cmd, text=True).strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"curl 失败 {algo_id}: {exc}") from exc
    wall = time.perf_counter() - t0
    parts = trailer.split()
    http_code = parts[0] if parts else "000"
    curl_s = float(parts[1]) if len(parts) > 1 else wall
    try:
        body = json.loads(tmp.read_text(encoding="utf-8"))
    except Exception:
        body = {"success": False, "message": tmp.read_text(encoding="utf-8")[:500], "data": {}, "files": {}}
    ok = http_code == "200" and bool(body.get("success"))
    TIMINGS.append(
        {
            "algorithm_id": algo_id,
            "file": file.name,
            "file2": None if file2 is None else file2.name,
            "http": http_code,
            "curl_s": round(curl_s, 4),
            "wall_s": round(wall, 4),
            "success": ok,
            "message": body.get("message", ""),
        }
    )
    log.info(
        "  HTTP %s  curl=%.3fs  客户端=%.3fs  %s",
        http_code,
        curl_s,
        wall,
        "OK" if ok else "FAIL",
    )
    if body.get("message"):
        log.info("  服务：%s", body["message"])
    if not ok:
        raise RuntimeError(f"{algo_id} 失败：http={http_code} {body.get('message')}")
    return body


def _copy_file(body: dict, key: str, dest: Path) -> Path:
    src = body.get("files", {}).get(key)
    if not src:
        raise KeyError(f"响应缺少 files.{key}，实际 {list((body.get('files') or {}).keys())}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    log.info("  产物 %s → %s", key, dest.name)
    return dest


def _pos_at(frames: list[dict], t: float) -> dict:
    return min(frames, key=lambda f: abs(float(f["time"]) - t))


def _log_timings() -> None:
    if not TIMINGS:
        return
    log.info("")
    log.info("========== API 耗时汇总 ==========")
    log.info("  %-28s %8s %8s  %s", "算法", "curl秒", "客户端秒", "输入")
    grouped: dict[str, list[dict]] = {}
    for row in TIMINGS:
        grouped.setdefault(row["algorithm_id"], []).append(row)
        log.info(
            "  %-28s %8.3f %8.3f  %s",
            row["algorithm_id"],
            row["curl_s"],
            row["wall_s"],
            row["file"],
        )
    log.info("  ----")
    for aid, rows in grouped.items():
        total = sum(r["curl_s"] for r in rows)
        log.info("  %-28s %8.3f  (%d 次)", aid, total, len(rows))
    log.info("  合计 curl  %.3fs  /  %d 次调用", sum(r["curl_s"] for r in TIMINGS), len(TIMINGS))
    calib = SCALE_INFO.get("calib") or {}
    shape = SCALE_INFO.get("mosaic_shape")
    target = float(calib.get("target_km2") or TARGET_KM2)
    target_gsd = float(calib.get("target_gsd_m") or TARGET_GSD_M)
    if shape and len(shape) >= 2:
        h, w = int(shape[0]), int(shape[1])
        pixels = h * w
        target_px = target * 1e6 / (target_gsd ** 2)
        scale = target_px / pixels if pixels else float("nan")
        uav_km2 = pixels * target_gsd * target_gsd / 1e6
        heavy_ids = {"16_orthorectify", "17_mosaic", "19_multi_source_register"}
        heavy = [r for r in TIMINGS if r["algorithm_id"] in heavy_ids]
        fixed = 0.012 * len(heavy)
        compute = max(sum(r["curl_s"] for r in heavy) - fixed, 0.0)
        est = compute * scale
        log.info("")
        log.info("========== 外推 %s km² @ %.2f m ==========", target, target_gsd)
        log.info("  本趟镶嵌 %d×%d = %d 像元（等价 UAV %.2fm 下 %.3f km²）", w, h, pixels, target_gsd, uav_km2)
        log.info("  像元倍数 ×%.1f", scale)
        log.info("  #16/#17/#19 纯计算≈%.1fs（已扣 HTTP 固定开销）", compute)
        log.info("  估 %s km² 墙钟 ≈ %.0fs（%.1f 分钟）", target, est, est / 60.0)
        log.info("  口径：两路正射应并行；本趟若串行，估时偏保守。内存不够时大测区会再变慢。")
        SCALE_INFO["estimate"] = {
            "test_pixels": pixels,
            "uav_equiv_km2": uav_km2,
            "target_km2": target,
            "target_gsd_m": target_gsd,
            "scale": scale,
            "heavy_compute_s": compute,
            "estimate_s": est,
        }
    payload = {"timings": TIMINGS, "scale": SCALE_INFO.get("estimate")}
    _write_json(WORK / "timings.json", payload)
    _write_json(OUT / "timings.json", payload)


def run() -> None:
    TIMINGS.clear()
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    _ping_api()
    calib = _load_json(CALIB_FILE)
    SCALE_INFO.clear()
    SCALE_INFO["calib"] = calib
    rgb_paths = list(RGB_FILES)
    ms_paths = list(MS_FILES)
    dark_file = DARK_FILE if DARK_FILE is not None and DARK_FILE.exists() else None

    with step("#04 架次过曝与场景统计质检"):
        reports = {}
        for path, bit in [(p, 8) for p in rgb_paths] + [(p, int(calib["bit_depth"])) for p in ms_paths]:
            body = api_run(
                "04_flight_qc",
                path,
                params={"bit_depth": bit, "max_saturated_ratio": 0.08},
            )
            dest = WORK / "04_qc" / f"{path.stem}_qc.json"
            _copy_file(body, "report_json", dest)
            data = body.get("data") or {}
            reports[path.name] = data
            log.info(
                "  %s  通过=%s  过曝=%s  SNR中位=%s",
                path.name,
                data.get("passed"),
                data.get("saturated_ratio"),
                data.get("snr_median"),
            )
        _write_json(WORK / "04_qc" / "qc_report.json", reports)

    with step("#02 同步曝光与时间戳对齐"):
        body = api_run("02_sync_timestamp", TIMESTAMPS)
        aligned_path = _copy_file(body, "aligned_json", WORK / "02_sync" / "aligned_frames.json")
        aligned = _load_json(aligned_path)
        data = body.get("data") or {}
        log.info(
            "  对齐 %s 条  钟差=%ss",
            data.get("n_aligned"),
            data.get("clock_offset_rgb_s"),
        )
        for row in aligned.get("rows") or []:
            pos = row.get("pos") or {}
            log.info(
                "  %s ts=%s → RGB %s  POS lon=%s lat=%s",
                row.get("hsi_id"),
                row.get("hsi_ts"),
                row.get("rgb_id"),
                pos.get("lon"),
                pos.get("lat"),
            )

    with step("#03 POS轨迹平滑与杠杆臂校正"):
        body = api_run(
            "03_pos_solution",
            POS_CSV,
            params={
                "alpha": 0.85,
                "lever_e_m": calib["lever_e_m"],
                "lever_n_m": calib["lever_n_m"],
                "lever_u_m": calib["lever_u_m"],
            },
        )
        pos_json = _copy_file(body, "pos_json", WORK / "03_pos" / "pos_frames.json")
        _copy_file(body, "pos_csv", WORK / "03_pos" / "pos_frames.csv")
        pos_out = _load_json(pos_json)
        pos_frames = pos_out["frames"]
        f0, f1 = pos_frames[0], pos_frames[-1]
        log.info("  采样 %d 条  粗差 %s", len(pos_frames), pos_out.get("n_outlier"))
        log.info(
            "  起点 %.5fE, %.5fN → 终点 %.5fE, %.5fN",
            f0["lon"],
            f0["lat"],
            f1["lon"],
            f1["lat"],
        )

    refl_paths: list[tuple[Path, dict]] = []
    for ms_path, row in zip(ms_paths, aligned["rows"]):
        with step(f"#06 暗电流校正  {ms_path.name}"):
            body = api_run("06_dark_current", ms_path, dark_file)
            dark_out = _copy_file(body, "cube_tif", WORK / "06_dark" / f"{ms_path.stem}_dark.tif")
            data = body.get("data") or {}
            log.info("  方法=%s  暗电流均值=%s", data.get("method"), data.get("mean"))

        with step(f"#10 辐射定标 DN→辐亮度  {ms_path.name}"):
            body = api_run(
                "10_radiance_calibration",
                dark_out,
                params={"gain": calib["gain"], "offset": calib["offset"]},
            )
            rad_out = _copy_file(body, "radiance_tif", WORK / "10_rad" / f"{ms_path.stem}_rad.tif")
            data = body.get("data") or {}
            log.info("  范围=[%s, %s]", data.get("min"), data.get("max"))

        with step(f"#12 白板/灰板反射率定标  {ms_path.name}"):
            body = api_run(
                "12_panel_reflectance",
                rad_out,
                params={"panel_reflectance": calib["panel_reflectance"]},
            )
            refl_out = _copy_file(body, "reflectance_tif", WORK / "12_refl" / f"{ms_path.stem}_refl.tif")
            refl_paths.append((refl_out, row))
            data = body.get("data") or {}
            log.info("  反射率均值=%s  范围=[%s, %s]", data.get("mean"), data.get("min"), data.get("max"))

    ms_ortho: list[Path] = []
    rgb_ortho: list[Path] = []
    gsd_m_out = calib.get("gsd_m")
    tile_rows = int(calib.get("tile_rows", 256))
    tile_size = int(calib.get("tile_size", 1024))
    ortho_params_base = {
        "focal_mm": calib["focal_mm"],
        "pixel_um": calib["pixel_um"],
        "tile_rows": tile_rows,
        "workers": 0,
    }
    if gsd_m_out is not None:
        ortho_params_base["gsd_out"] = gsd_m_out
    geo_params = {"gsd_m": gsd_m_out} if gsd_m_out is not None else {}

    for (refl_path, row), rgb_path in zip(refl_paths, rgb_paths):
        pos_hit = _pos_at(pos_frames, float(row["hsi_ts"]))
        pos_file = WORK / "15_geo" / f"{rgb_path.stem}_pos.json"
        _write_json(
            pos_file,
            {"pos": {"lon": pos_hit["lon"], "lat": pos_hit["lat"], "alt": pos_hit["alt"], "yaw": pos_hit["yaw"]}},
        )
        ortho_params = {
            **ortho_params_base,
            "alt_m": pos_hit["alt"],
            "roll": pos_hit["roll"],
            "pitch": pos_hit["pitch"],
            "yaw": pos_hit["yaw"],
        }

        with step(f"#15 POS中心点与GSD粗定位  {refl_path.name}"):
            body = api_run("15_geo_locate", refl_path, pos_file, geo_params)
            ms_geo = _copy_file(body, "cube_tif", WORK / "15_geo" / f"{refl_path.stem}_geo.tif")
            data = body.get("data") or {}
            log.info("  中心 %sE, %sN  GSD=%sm", data.get("lon"), data.get("lat"), data.get("gsd_m"))

        with step(f"#15 POS中心点与GSD粗定位  {rgb_path.name}"):
            body = api_run("15_geo_locate", rgb_path, pos_file, geo_params)
            rgb_geo = _copy_file(body, "cube_tif", WORK / "15_geo" / f"{rgb_path.stem}_geo.tif")

        with step(f"#16 正射校正  {ms_geo.name}"):
            body = api_run("16_orthorectify", ms_geo, DEM_FILE, ortho_params)
            dest = _copy_file(body, "ortho_tif", WORK / "16_ortho" / f"{refl_path.stem}_ortho.tif")
            ms_ortho.append(dest)
            data = body.get("data") or {}
            log.info("  方法=%s  workers=%s", data.get("method"), data.get("workers"))

        with step(f"#16 正射校正  {rgb_geo.name}（RGB 不经 L1）"):
            body = api_run("16_orthorectify", rgb_geo, DEM_FILE, ortho_params)
            dest = _copy_file(body, "ortho_tif", WORK / "16_ortho" / f"{rgb_path.stem}_ortho.tif")
            rgb_ortho.append(dest)

    with step("#17 影像匹配与镶嵌  8 通道"):
        body = api_run("17_mosaic", ms_ortho[0], ms_ortho[1], {"tile_size": tile_size, "workers": 0})
        hsi_mosaic = _copy_file(body, "mosaic_tif", WORK / "17_mosaic" / "hsi_mosaic.tif")
        data = body.get("data") or {}
        SCALE_INFO["mosaic_shape"] = data.get("shape")
        log.info("  8 通道  %s 景  形状=%s", data.get("n_scenes"), data.get("shape"))

    with step("#17 影像匹配与镶嵌  RGB"):
        body = api_run("17_mosaic", rgb_ortho[0], rgb_ortho[1], {"tile_size": tile_size, "workers": 0})
        rgb_mosaic = _copy_file(body, "mosaic_tif", WORK / "17_mosaic" / "rgb_mosaic.tif")
        data = body.get("data") or {}
        log.info("  RGB  %s 景  形状=%s", data.get("n_scenes"), data.get("shape"))

    with step("#19 HSI-RGB全局平移配准"):
        body = api_run(
            "19_multi_source_register",
            hsi_mosaic,
            rgb_mosaic,
            {"tile_size": max(tile_size // 2, 128), "workers": 0},
        )
        hsi_out = _copy_file(body, "hsi_tif", OUT / "hsi_ref.tif")
        rgb_out = _copy_file(body, "rgb_aligned_tif", OUT / "rgb_aligned.tif")
        data = body.get("data") or {}
        log.info(
            "  方法=%s  dy=%s dx=%s  峰值=%s",
            data.get("method"),
            data.get("dy"),
            data.get("dx"),
            data.get("peak_response"),
        )
        log.info("  交付 %s", hsi_out)
        log.info("  交付 %s", rgb_out)

    _log_timings()
    log.info("")
    log.info("全部完成。QGIS 打开本趟 out 目录叠看套合。")


def _require_file_args(paths: list[Path], label: str) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        if path.is_dir():
            raise SystemExit(f"{label} 请指定文件，不要目录：{path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        out.append(path)
    return out


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RGB + 8 通道：经算法 API 跑到 #19。输入全部是文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python run_fusion.py --bench \\\n"
            "    --rgb rgb_0001.tif rgb_0002.tif \\\n"
            "    --ms  ms_0001.tif ms_0002.tif \\\n"
            "    --pos pos.csv --timestamps timestamps.json \\\n"
            "    --dem dem.tif --calib calib.json\n"
        ),
    )
    p.add_argument("--rgb", nargs="+", type=Path, required=True, help="可见光 3 通道 .tif（两景）")
    p.add_argument("--ms", nargs="+", type=Path, required=True, help="8 通道 DN .tif（两景）")
    p.add_argument("--pos", type=Path, required=True, help="POS 轨迹 CSV")
    p.add_argument("--timestamps", type=Path, required=True, help="三路时间戳 JSON")
    p.add_argument("--dem", type=Path, required=True, help="DEM GeoTIFF")
    p.add_argument("--calib", type=Path, required=True, help="定标/相机 JSON")
    p.add_argument("--dark", type=Path, default=None, help="可选暗电流帧 .tif")
    p.add_argument(
        "--bench",
        action="store_true",
        help="Sentinel-2 真景条带，按像元外推 12 km² @ 0.3 m；写入上面列出的文件",
    )
    p.add_argument("--force", action="store_true", help="覆盖已有输入，重新准备")
    p.add_argument("--prepare-only", action="store_true", help="只准备输入，不调 API")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    global WORK, OUT, RGB_FILES, MS_FILES, DARK_FILE, POS_CSV, TIMESTAMPS, DEM_FILE, CALIB_FILE
    args = _parse_args(argv)
    _setup_log()
    RGB_FILES = _require_file_args(args.rgb, "--rgb")
    MS_FILES = _require_file_args(args.ms, "--ms")
    POS_CSV = _require_file_args([args.pos], "--pos")[0]
    TIMESTAMPS = _require_file_args([args.timestamps], "--timestamps")[0]
    DEM_FILE = _require_file_args([args.dem], "--dem")[0]
    CALIB_FILE = _require_file_args([args.calib], "--calib")[0]
    DARK_FILE = _require_file_args([args.dark], "--dark")[0] if args.dark else None
    if len(RGB_FILES) != len(MS_FILES):
        raise SystemExit(f"--rgb {len(RGB_FILES)} 景与 --ms {len(MS_FILES)} 景数量必须一致")
    log.info("融合测试  %s", ROOT)
    log.info("RGB   %s", "  ".join(str(p) for p in RGB_FILES))
    log.info("MS    %s", "  ".join(str(p) for p in MS_FILES))
    log.info("POS   %s", POS_CSV)
    log.info("时间戳 %s", TIMESTAMPS)
    log.info("DEM   %s", DEM_FILE)
    log.info("定标  %s", CALIB_FILE)
    if DARK_FILE is not None:
        log.info("暗电流 %s", DARK_FILE)
    log.info("API   %s", API)
    if args.bench:
        log.info(
            "模式  Sentinel-2 真景条带  帧 %dx%d  外推 %.0f km² @ %.2fm",
            BENCH_H,
            BENCH_W,
            TARGET_KM2,
            TARGET_GSD_M,
        )
        prepare_bench_files(force=args.force)
    else:
        prepare_start_files(force=args.force)
    if args.prepare_only:
        return 0
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    WORK = ROOT / "work" / stamp
    OUT = ROOT / "out" / stamp
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    _attach_run_log(WORK / "run.log")
    _attach_run_log(OUT / "run.log")
    log.info("本趟  %s", stamp)
    log.info("中间  %s", WORK)
    log.info("交付  %s", OUT)
    t0 = time.perf_counter()
    run()
    log.info("墙钟 %.1fs", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
