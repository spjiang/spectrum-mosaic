"""对标诊断：把自研 DSM / 正射与商业成品的关键量放在一起打印。

只读，不写成果。用来回答「GSD 偏大是哪来的」「DSM 为什么显示发白」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio

REF = Path("/data/input/MAX_20251017/拼图结果")
CACHE = Path("/data/output/runs/demo_max_20251017_full/20260919_202140/cache/at_result.npz")


def stats(name: str, z: np.ndarray) -> None:
    ok = np.isfinite(z)
    v = z[ok]
    if v.size == 0:
        print(f"{name}: 全空")
        return
    qs = np.percentile(v, [0.1, 1, 2, 10, 50, 90, 98, 99, 99.9])
    print(
        f"{name}: n={v.size} 有效={ok.mean():.4f} min={v.min():.2f} max={v.max():.2f} "
        f"mean={v.mean():.2f} std={v.std():.2f}"
    )
    print("    p0.1/p1/p2/p10/p50/p90/p98/p99/p99.9 = " + " / ".join(f"{q:.2f}" for q in qs))


def read_dsm(path: Path):
    with rasterio.open(path) as ds:
        z = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
        return z, ds.transform, ds.width, ds.height


def main(ours_dir: str) -> int:
    ours = Path(ours_dir)
    print("=== DSM 灰度分布（决定 QGIS 拉伸后的明暗）===")
    zc, tc, wc, hc = read_dsm(REF / "DSM.tif")
    zo, to, wo, ho = read_dsm(ours / "DSM.tif")
    stats("商业 DSM", zc)
    stats("自研 DSM", zo)
    print(f"商业 GSD={tc.a:.9f} size={wc}x{hc} 原点=({tc.c:.3f},{tc.f:.3f})")
    print(f"自研 GSD={to.a:.9f} size={wo}x{ho} 原点=({to.c:.3f},{to.f:.3f})")
    print(f"GSD 比 自研/商业 = {to.a / tc.a:.6f}")

    print()
    print("=== 空三结果：航高与焦距 ===")
    if not CACHE.is_file():
        print(f"缺少空三检查点 {CACHE}")
        return 1
    with np.load(CACHE, allow_pickle=False) as d:
        meta = json.loads(bytes(d["meta"]).decode("utf-8"))
        pose_c = np.asarray(d["pose_C"], float)
        pts = np.asarray(d["points"], float)
    print("meta.crs=", meta.get("crs"), " meta.ground_z=", meta.get("ground_z"))
    print("meta.camera_key=", meta.get("camera_key"))
    for cam in meta.get("cameras") or []:
        print(
            f"  camera {cam['key']}: {cam['width']}x{cam['height']} f={cam['f']:.3f}px "
            f"cx={cam['cx']:.2f} cy={cam['cy']:.2f} k1={cam['k1']:.5f}"
        )
    print("meta.stats=", meta.get("stats"))

    cam_z = pose_c[:, 2]
    stats("相机高程 camera_z", cam_z)
    stats("稀疏点高程 sparse_z", pts[:, 2])

    ground_med = float(np.median(pts[:, 2]))
    cam_med = float(np.median(cam_z))
    h_med = float(np.median(cam_z - ground_med))
    cams = {c["key"]: c for c in meta.get("cameras") or []}
    primary = cams.get(meta.get("camera_key") or "Color") or next(iter(cams.values()))
    f_px = float(primary["f"])
    print()
    print("=== GSD 反推 ===")
    print(f"主相机 f = {f_px:.3f} px")
    print(f"当前 ground_z = median(sparse_z) = {ground_med:.2f}")
    print(f"当前 H = median(camera_z - ground_z) = {h_med:.2f} m")
    print(f"当前 正射GSD = H/f = {h_med / f_px:.7f}  (实测 {to.a / 2:.7f})")
    print(f"商业 正射GSD = 0.0538736470 → 等价 H = {0.0538736470 * f_px:.2f} m")
    print(f"商业 DSM GSD = 0.1077472933 → 等价 H = {0.1077472933 * f_px / 2:.2f} m")

    print()
    print("=== 参考面假设检验：H = median(camera_z) - z_ref ===")
    zc_v = zc[np.isfinite(zc)]
    zo_v = zo[np.isfinite(zo)]
    cands = [
        ("median(商业DSM)", float(np.median(zc_v))),
        ("mean(商业DSM)", float(np.mean(zc_v))),
        ("p75(商业DSM)", float(np.percentile(zc_v, 75))),
        ("p90(商业DSM)", float(np.percentile(zc_v, 90))),
        ("max(商业DSM)", float(np.max(zc_v))),
        ("median(sparse)", ground_med),
        ("p90(sparse)", float(np.percentile(pts[:, 2], 90))),
        ("median(自研DSM)", float(np.median(zo_v))),
    ]
    print(f"median(camera_z) = {cam_med:.2f}")
    for label, zref in cands:
        h = cam_med - zref
        print(
            f"  z_ref={label:<16} {zref:9.2f} → H={h:8.2f} → 正射GSD={h / f_px:.7f} "
            f"(商业比 {(h / f_px) / 0.0538736470:.4f})"
        )
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "/data/output/runs/demo_max_20251017_dsmfix/拼图结果"
    raise SystemExit(main(arg))
