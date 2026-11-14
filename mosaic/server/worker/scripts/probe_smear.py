"""量油彩窗：DSM 短波起伏、贴边低通后还剩多少、到空值的距离。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from scipy.ndimage import uniform_filter

from ms_mosaic.dsm import flatten_edge_z

DSM = Path("/data/output/runs/demo_max_20251017_restore/拼图结果/DSM.tif")
RGB = Path("/data/output/runs/demo_max_20251017_restore/拼图结果/Orthomosaic_pix_surf_group0.tif")
REF = Path("/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif")
COMM_DSM = Path("/data/input/MAX_20251017/拼图结果/DSM.tif")

WINDOWS = (
    ("右上北", 674607.2, 2620470.2, 40.0),
    ("右上南", 674601.6, 2620222.0, 40.0),
    ("东缘", 674600.0, 2620230.0, 50.0),
    ("北缘", 674260.0, 2620540.0, 50.0),
    ("内部", 674250.0, 2620200.0, 40.0),
)


def local_amp(z: np.ndarray, rad: int) -> np.ndarray:
    """窗口内高程相对局部均值的标准差，短波起伏越大油彩越重。"""
    ok = np.isfinite(z)
    fill = np.where(ok, z, 0.0)
    cnt = uniform_filter(ok.astype(np.float64), rad)
    mean = uniform_filter(fill, rad) / np.maximum(cnt, 1e-6)
    var = uniform_filter(np.where(ok, (z - mean) ** 2, 0.0), rad) / np.maximum(cnt, 1e-6)
    return np.where(ok & (cnt > 0.5), np.sqrt(np.maximum(var, 0.0)), np.nan)


def read_win(path: Path, x: float, y: float, span: float, band: int = 1):
    with rasterio.open(path) as ds:
        w = from_bounds(x - span / 2, y - span / 2, x + span / 2, y + span / 2, ds.transform)
        w = w.round_offsets().round_lengths()
        arr = ds.read(band, window=w, boundless=True, fill_value=0).astype(np.float64)
        nodata = ds.nodata
        if nodata is not None:
            arr = np.where(arr == nodata, np.nan, arr)
        if band == 1:
            arr = np.where(arr < -1e6, np.nan, arr)
        return arr, float(ds.transform.a)


def main() -> None:
    with rasterio.open(DSM) as ds:
        z = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            z = np.where(z == ds.nodata, np.nan, z)
        z = np.where(z < -1e6, np.nan, z)
        gsd = float(ds.transform.a)
        tr = ds.transform
    flat = flatten_edge_z(z, gsd, win_m=40.0, band_m=80.0)
    # 更狠的常值面：整幅中位，只写回 80 m 带，用来对照「窗口实验里有效的做法」
    med = float(np.nanmedian(z))
    from scipy.ndimage import distance_transform_edt

    dist = distance_transform_edt(np.isfinite(z)) * gsd
    const = np.where(np.isfinite(z) & (dist <= 80.0), med, z)

    print(f"dsm gsd={gsd:.4f} finite={np.isfinite(z).mean():.3f}", flush=True)
    print(
        f"flatten changed {(np.isfinite(z) & (np.abs(flat - z) > 0.05)).mean():.3f} "
        f"const changed {(np.isfinite(z) & (np.abs(const - z) > 0.05)).mean():.3f}",
        flush=True,
    )

    for name, x, y, span in WINDOWS:
        zw, _ = read_win(DSM, x, y, span)
        cw, _ = read_win(COMM_DSM, x, y, span)
        rgb, rgsd = read_win(RGB, x, y, span, band=2)
        alpha, _ = read_win(RGB, x, y, span, band=4)
        ref_a, _ = read_win(REF, x, y, span, band=4)
        # 地理窗对到 DSM 行列
        col = int(round((x - tr.c) / tr.a))
        row = int(round((y - tr.f) / tr.e))
        half = int(round(span / 2 / gsd))
        r0, r1 = max(0, row - half), min(z.shape[0], row + half)
        c0, c1 = max(0, col - half), min(z.shape[1], col + half)
        zz, ff, dd = z[r0:r1, c0:c1], flat[r0:r1, c0:c1], dist[r0:r1, c0:c1]
        amp = local_amp(zz, max(3, int(round(8.0 / gsd))))
        amp_f = local_amp(ff, max(3, int(round(8.0 / gsd))))
        hh, ww = min(zz.shape[0], cw.shape[0]), min(zz.shape[1], cw.shape[1])
        both = np.isfinite(zz[:hh, :ww]) & np.isfinite(cw[:hh, :ww])
        bias = float(np.nanmedian(zz[:hh, :ww][both] - cw[:hh, :ww][both])) if both.any() else float("nan")
        extra = (alpha > 0) & ~(ref_a[: alpha.shape[0], : alpha.shape[1]] > 0)
        print(
            f"\n{name} dist_p50={np.nanmedian(dd):.1f}m "
            f"z_std={np.nanstd(zz):.2f} flat_std={np.nanstd(ff):.2f} "
            f"amp8_p50={np.nanmedian(amp):.2f}->{np.nanmedian(amp_f):.2f} "
            f"bias_vs_comm={bias:.1f}m "
            f"rgb_extra={int(extra.sum())}/{int((alpha > 0).sum())}",
            flush=True,
        )


if __name__ == "__main__":
    main()
