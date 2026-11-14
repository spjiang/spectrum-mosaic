"""密集匹配小窗口验证台。

复用已有空三检查点，在测区内部截一个小窗口跑 S3+S4，直接和商业 DSM 比
解出率、偏差、中误差、局部起伏。改 dense/dsm 后先过这里，再决定要不要全量重跑。

用法：
    python /app/scripts/probe_dense.py [窗口边长米] [中心X] [中心Y]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy.ndimage import uniform_filter

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.dense import DenseConfig, compute_height_field
from ms_mosaic.dsm import build_dsm
from ms_mosaic.grid import (
    Grid,
    estimate_dsm_gsd,
    estimate_z_margin_m,
    grid_from_footprints,
    ground_reference_z,
)

CACHE = Path("/data/output/runs/demo_max_20251017_full/20260919_202140/cache/at_result.npz")
REF_DSM = Path("/data/input/MAX_20251017/拼图结果/DSM.tif")

_CAM_FIELDS = ("key", "width", "height", "f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2", "model")


def load_at():
    with np.load(CACHE, allow_pickle=False) as d:
        meta = json.loads(bytes(d["meta"]).decode("utf-8"))
        ids = [int(i) for i in d["pose_ids"]]
        pr = np.asarray(d["pose_R"], float)
        pc = np.asarray(d["pose_C"], float)
        pts = np.asarray(d["points"], float)
    poses = {i: Pose(rotation=pr[k], center=pc[k]) for k, i in enumerate(ids)}
    cam_raw = {c["key"]: c for c in meta["cameras"]}
    primary = cam_raw[meta.get("camera_key") or "Color"]
    camera = Camera(**{k: primary[k] for k in _CAM_FIELDS})
    img_ids = [int(i) for i in meta["image_ids"]]
    paths = {i: Path(p) for i, p in zip(img_ids, meta["image_paths"])}
    return camera, poses, pts, paths, meta


def roughness(z: np.ndarray, win: int) -> float:
    ok = np.isfinite(z).astype(np.float32)
    zz = np.nan_to_num(z, nan=0.0)
    m = uniform_filter(zz, win, mode="nearest")
    d = uniform_filter(ok, win, mode="nearest")
    mean = np.where(d > 0.5, m / np.maximum(d, 1e-6), np.nan)
    r = np.abs(z - mean)
    return float(np.nanmean(r))


def _fill(z: np.ndarray) -> np.ndarray:
    """补洞后再滤波，避免 nan 把邻域算子污染。"""
    from ms_mosaic.dsm import fill_holes

    return fill_holes(np.asarray(z, np.float64))


def _sweep_filters(field, dsm, ref, report) -> None:
    """扫一遍候选后处理，靠实测挑方案。"""
    from scipy.ndimage import gaussian_filter, median_filter

    conf = np.asarray(field.confidence, np.float64)
    print("\n--- 剔除强度（后接 中值5+高斯1.5）---")
    for conf_min in (0.06, 0.10, 0.16, 0.24):
        for tol in (1.5, 2.0, 3.0):
            z, kept = _reject(field.z, conf, conf_min, tol)
            v2 = np.isfinite(z)
            med = median_filter(np.where(v2, z, np.nanmedian(z[v2])), size=5, mode="nearest")
            report(
                f"置信>{conf_min:.2f} 一致{tol:.1f}m 保留{kept:.0%}",
                np.where(v2, gaussian_filter(med, 1.5, mode="nearest"), np.nan),
            )

    print("\n--- 固定剔除(置信>0.10, 一致2.0m)，比较平滑核 ---")
    z, kept = _reject(field.z, conf, 0.10, 2.0)
    v2 = np.isfinite(z)
    pad = np.where(v2, z, np.nanmedian(z[v2]))
    report("不平滑", np.where(v2, z, np.nan))
    for size in (3, 5):
        med = median_filter(pad, size=size, mode="nearest")
        report(f"中值{size}", np.where(v2, med, np.nan))
        for sig in (0.6, 1.0, 1.5):
            report(
                f"中值{size}+高斯{sig}",
                np.where(v2, gaussian_filter(med, sig, mode="nearest"), np.nan),
            )
    for sr in (0.3, 0.6, 1.2):
        report(f"双边 空间2 值域{sr}", np.where(v2, _bilateral(pad, 2.0, sr), np.nan))


def _reject(z_in: np.ndarray, conf: np.ndarray, conf_min: float, tol_m: float):
    """置信度门限 + 与大窗口稳健面的一致性检查，剔除后补洞。返回 (z, 保留比例)。"""
    from scipy.ndimage import median_filter

    z = np.where(np.isfinite(z_in), z_in, np.nan).astype(np.float64)
    n0 = int(np.isfinite(z).sum())
    if conf_min > 0:
        z = np.where(np.isfinite(conf) & (conf < conf_min), np.nan, z)
    ok = np.isfinite(z)
    if int(ok.sum()) >= 100:
        proxy = np.where(ok, z, np.nanmedian(z[ok]))
        surf = median_filter(proxy, size=15, mode="nearest")
        z = np.where(ok & (np.abs(z - surf) > tol_m), np.nan, z)
    kept = int(np.isfinite(z).sum()) / max(n0, 1)
    return _fill(z), kept


def _bilateral(z: np.ndarray, sigma_s: float, sigma_r: float) -> np.ndarray:
    """双边滤波（Tomasi & Manduchi, ICCV 1998）：压噪声但保留陡坎。"""
    from scipy.ndimage import gaussian_filter

    rad = max(1, int(round(2.0 * sigma_s)))
    num = np.zeros_like(z)
    den = np.zeros_like(z)
    guide = gaussian_filter(z, 1.0, mode="nearest")
    for dr in range(-rad, rad + 1):
        for dc in range(-rad, rad + 1):
            shifted = np.roll(np.roll(z, dr, axis=0), dc, axis=1)
            ws = np.exp(-(dr * dr + dc * dc) / (2.0 * sigma_s * sigma_s))
            wr = np.exp(-((shifted - guide) ** 2) / (2.0 * sigma_r * sigma_r))
            w = ws * wr
            num += shifted * w
            den += w
    return num / np.maximum(den, 1e-9)


def main() -> int:
    span = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    camera, poses, pts, paths, meta = load_at()
    ground_z = ground_reference_z(pts)
    cams = {i: camera for i in poses}
    gsd = estimate_dsm_gsd(camera, poses, ground_z)
    full = grid_from_footprints(cams, poses, ground_z, gsd, meta["crs"])
    cx = float(sys.argv[2]) if len(sys.argv) > 3 else (full.bounds[0] + full.bounds[2]) / 2
    cy = float(sys.argv[3]) if len(sys.argv) > 3 else (full.bounds[1] + full.bounds[3]) / 2
    sub = Grid.from_bounds((cx - span / 2, cy - span / 2, cx + span / 2, cy + span / 2), gsd, full.crs)
    print(f"窗口 {sub.width}x{sub.height} @ {sub.gsd:.4f} m 中心=({cx:.1f},{cy:.1f})")

    inside = (
        (pts[:, 0] > sub.bounds[0] - 60)
        & (pts[:, 0] < sub.bounds[2] + 60)
        & (pts[:, 1] > sub.bounds[1] - 60)
        & (pts[:, 1] < sub.bounds[3] + 60)
    )
    sub_pts = pts[inside]
    print(f"窗口内稀疏点 {sub_pts.shape[0]}")

    cfg = DenseConfig(workers=int(sys.argv[4]) if len(sys.argv) > 4 else 6)
    cfg.z_margin_m = estimate_z_margin_m(pts)
    if len(sys.argv) > 5:
        cfg.refine_levels = int(sys.argv[5])
    t = time.time()
    field = compute_height_field(sub, sub_pts, cams, poses, paths, cfg=cfg, log=print)
    print(f"密集匹配 {time.time() - t:.1f}s  解出 {np.isfinite(field.z).mean():.1%}")
    dsm = build_dsm(field, coverage=None, max_fill_gap_m=None)

    ref = np.full(sub.shape, np.nan, np.float32)
    with rasterio.open(REF_DSM) as ds:
        z = ds.read(1).astype(np.float32)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        reproject(
            z, ref, src_transform=ds.transform, src_crs=ds.crs, src_nodata=np.nan,
            dst_transform=sub.transform, dst_crs=sub.crs, dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    def report(name: str, arr: np.ndarray) -> None:
        ok = np.isfinite(arr) & np.isfinite(ref)
        if not ok.any():
            print(f"{name}: 无重叠")
            return
        d = arr[ok] - ref[ok]
        print(
            f"{name:<22} 解出={np.isfinite(arr).mean():5.1%} 偏差={d.mean():+6.3f} "
            f"中误差={d.std():6.3f} |d|p90={np.percentile(np.abs(d), 90):5.2f} "
            f"粗差>5m={np.mean(np.abs(d) > 5):6.2%} "
            f"起伏3/5/11={roughness(arr, 3):.3f}/{roughness(arr, 5):.3f}/{roughness(arr, 11):.3f}"
        )

    print(f"{'商业参考':<22} 起伏3/5/11={roughness(ref, 3):.3f}/{roughness(ref, 5):.3f}/{roughness(ref, 11):.3f} std={np.nanstd(ref):.2f}")
    report("原始高程场", field.z)
    report("成品 DSM(现状)", dsm.z)

    # 合理高程带的来源对比。ref_z=None 时退化成用 DSM 自身中值 ± max(80, 3·MAD)，
    # 山顶会被整片裁掉再从低处补回来；给了空三点才用 p1−30 / p99+50。
    from ms_mosaic.dsm import plausible_z_limits

    cov = np.ones(sub.shape, bool)
    for tag, ref_z in (("高程带·DSM自身", None), ("高程带·空三点", sub_pts[:, 2])):
        lim = plausible_z_limits(ref_z, fallback_z=field.z)
        report(
            f"{tag} [{lim[0]:.0f},{lim[1]:.0f}]" if lim else tag,
            build_dsm(field, coverage=cov, max_fill_gap_m=None, ref_z=ref_z).z,
        )
    print(f"窗口商业高程 p50/p99/max = {np.nanpercentile(ref,[50,99]).round(1)} / {np.nanmax(ref):.1f}")
    _sweep_filters(field, dsm, ref, report)
    keys = (
        "fill_ratio", "mean_views", "image_gsd", "pyramid_level", "antialias_sigma_px",
        "coarse_gsd", "z_margin_m", "coarse_z_margin_m", "coarse_n_layers",
    )
    print("dense stats:", {k: field.stats[k] for k in keys if k in field.stats})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
