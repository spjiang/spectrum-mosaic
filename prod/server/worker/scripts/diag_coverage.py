"""对照商业成品，量化矩形画幅 / 白点 / 绿斑的证据。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import rowcol, xy
from scipy.ndimage import binary_fill_holes, label

OURS = Path(
    "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合"
    "/prod/runs/full_MAX_20251017_001/拼图结果"
)
COMM = Path(
    "/Users/jiangshengping/wwwroot/shenzhen/spectrum/algorithm/projects/数据融合"
    "/prod/docs/需求/测试正式数据/MAX_20251017/拼图结果"
)
BLOB_X, BLOB_Y = 674545.0, 2619976.0


def _profile(path: Path) -> dict:
    with rasterio.open(path) as ds:
        return {
            "size": (ds.width, ds.height),
            "gsd": float(ds.transform.a),
            "bounds": tuple(float(v) for v in ds.bounds),
            "crs": str(ds.crs),
            "count": ds.count,
            "nodata": ds.nodata,
            "aspect": ds.width / ds.height,
        }


def _mask_stats(valid: np.ndarray) -> dict:
    lab, n = label(valid)
    sizes = np.bincount(lab.ravel()) if n else np.array([0])
    main_id = int(np.argmax(sizes[1:]) + 1) if n else 0
    main = lab == main_id if n else valid
    filled = binary_fill_holes(main)
    holes = filled & ~main
    hlab, nh = label(holes)
    hs = np.bincount(hlab.ravel()) if nh else np.array([0])
    extra = valid & ~main
    elab, ne = label(extra)
    es = np.bincount(elab.ravel()) if ne else np.array([0])
    hole_sizes = hs[1:] if nh else np.array([], dtype=int)
    extra_sizes = es[1:] if ne else np.array([], dtype=int)
    return {
        "valid_px": int(valid.sum()),
        "valid_pct": float(valid.mean()),
        "n_cc": int(n),
        "main_px": int(main.sum()),
        "n_holes": int(nh),
        "hole_px": int(holes.sum()),
        "hole_size_p50": float(np.median(hole_sizes)) if hole_sizes.size else 0.0,
        "n_1px_holes": int((hole_sizes == 1).sum()),
        "n_le4_holes": int((hole_sizes <= 4).sum()),
        "n_extra_cc": int(ne),
        "extra_px": int(extra.sum()),
        "extra_size_max": int(extra_sizes.max()) if extra_sizes.size else 0,
        "extra_sizes_top5": [int(v) for v in sorted(extra_sizes, reverse=True)[:5]],
    }


def _cc_at(valid: np.ndarray, transform, x: float, y: float) -> dict:
    r, c = rowcol(transform, x, y)
    h, w = valid.shape
    info = {"row": int(r), "col": int(c), "inside_raster": 0 <= r < h and 0 <= c < w}
    if not info["inside_raster"]:
        return info
    lab, n = label(valid)
    cid = int(lab[r, c])
    info["cc_id"] = cid
    info["valid_here"] = bool(valid[r, c])
    if cid == 0 or n == 0:
        return info
    sizes = np.bincount(lab.ravel())
    main_id = int(np.argmax(sizes[1:]) + 1)
    ys, xs = np.nonzero(lab == cid)
    info.update(
        {
            "cc_size": int(sizes[cid]),
            "is_main": cid == main_id,
            "main_id": main_id,
            "main_size": int(sizes[main_id]),
            "centroid_x": float(xy(transform, float(ys.mean()), float(xs.mean()))[0]),
            "centroid_y": float(xy(transform, float(ys.mean()), float(xs.mean()))[1]),
        }
    )
    return info


def _window_rgb(path: Path, x: float, y: float, half: int = 80) -> dict:
    with rasterio.open(path) as ds:
        r, c = rowcol(ds.transform, x, y)
        r0, c0 = max(0, r - half), max(0, c - half)
        r1, c1 = min(ds.height, r + half), min(ds.width, c + half)
        win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
        arr = ds.read(window=win)
        alpha = arr[3] if ds.count >= 4 else np.full(arr.shape[1:], 255, np.uint8)
        valid = alpha > 0
        rgb = arr[:3]
        mean = rgb[:, valid].mean(axis=1) if valid.any() else np.array([0, 0, 0])
        return {
            "row": int(r),
            "col": int(c),
            "window": (int(r0), int(c0), int(r1), int(c1)),
            "valid_pct": float(valid.mean()),
            "mean_rgb": [float(v) for v in mean],
            "n_valid": int(valid.sum()),
        }


def _dsm_at(path: Path, x: float, y: float, half: int = 40) -> dict:
    with rasterio.open(path) as ds:
        r, c = rowcol(ds.transform, x, y)
        r0, c0 = max(0, r - half), max(0, c - half)
        r1, c1 = min(ds.height, r + half), min(ds.width, c + half)
        win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
        z = ds.read(1, window=win)
        nodata = ds.nodata
        ok = np.isfinite(z)
        if nodata is not None:
            ok &= z != nodata
        vals = z[ok]
        here = None
        if 0 <= r < ds.height and 0 <= c < ds.width:
            zr = ds.read(1, window=rasterio.windows.Window(c, r, 1, 1))[0, 0]
            here = None if (nodata is not None and zr == nodata) or not np.isfinite(zr) else float(zr)
        return {
            "row": int(r),
            "col": int(c),
            "inside": 0 <= r < ds.height and 0 <= c < ds.width,
            "z_here": here,
            "n_ok": int(ok.sum()),
            "z_min": float(vals.min()) if vals.size else None,
            "z_max": float(vals.max()) if vals.size else None,
            "z_median": float(np.median(vals)) if vals.size else None,
        }


def _hole_vs_dsm(rgb_path: Path, dsm_path: Path, n_sample: int = 4000) -> dict:
    with rasterio.open(rgb_path) as rgb, rasterio.open(dsm_path) as dsm:
        alpha = rgb.read(4)
        valid = alpha > 0
        lab, n = label(valid)
        sizes = np.bincount(lab.ravel())
        main = lab == int(np.argmax(sizes[1:]) + 1)
        filled = binary_fill_holes(main)
        holes = np.argwhere(filled & ~main)
        if holes.size == 0:
            return {"n_holes_px": 0}
        rng = np.random.default_rng(0)
        pick = holes[rng.choice(len(holes), size=min(n_sample, len(holes)), replace=False)]
        xs, ys = xy(rgb.transform, pick[:, 0], pick[:, 1], offset="center")
        xs = np.atleast_1d(np.asarray(xs, float))
        ys = np.atleast_1d(np.asarray(ys, float))
        rows, cols = rowcol(dsm.transform, xs, ys)
        rows = np.asarray(rows)
        cols = np.asarray(cols)
        z = dsm.read(1)
        nodata = dsm.nodata
        inside = (rows >= 0) & (rows < dsm.height) & (cols >= 0) & (cols < dsm.width)
        zz = np.full(len(rows), np.nan)
        zz[inside] = z[rows[inside], cols[inside]]
        ok = np.isfinite(zz)
        if nodata is not None:
            ok &= zz != nodata
        # 孔是否落在块缝上（正射块 384，paste 后步长 384）
        on_tile_row = (pick[:, 0] % 384) < 2
        on_tile_col = (pick[:, 1] % 384) < 2
        return {
            "n_holes_px": int((filled & ~main).sum()),
            "sampled": int(len(pick)),
            "dsm_invalid_frac": float((~ok).mean()),
            "on_tile_row_frac": float(on_tile_row.mean()),
            "on_tile_col_frac": float(on_tile_col.mean()),
        }


def main() -> None:
    ours_rgb = OURS / "Orthomosaic_pix_surf_group0.tif"
    comm_rgb = COMM / "Orthomosaic_pix_surf_group0.tif"
    ours_dsm = OURS / "DSM.tif"
    comm_dsm = COMM / "DSM.tif"

    print("=== PROFILE ===")
    for name, p in [("ours_rgb", ours_rgb), ("comm_rgb", comm_rgb), ("ours_dsm", ours_dsm), ("comm_dsm", comm_dsm)]:
        print(name, _profile(p))

    print("\n=== DSM GLOBAL Z ===")
    for name, p in [("ours_dsm", ours_dsm), ("comm_dsm", comm_dsm)]:
        with rasterio.open(p) as ds:
            z = ds.read(1)
            nodata = ds.nodata
            ok = np.isfinite(z)
            if nodata is not None:
                ok &= z != nodata
            vals = z[ok]
            print(
                name,
                {
                    "valid_pct": float(ok.mean()),
                    "z_min": float(vals.min()),
                    "z_p01": float(np.percentile(vals, 1)),
                    "z_p50": float(np.median(vals)),
                    "z_p99": float(np.percentile(vals, 99)),
                    "z_max": float(vals.max()),
                    "n_below_1000": int((vals < 1000).sum()),
                    "n_below_0": int((vals < 0).sum()),
                    "n_above_2000": int((vals > 2000).sum()),
                },
            )

    print("\n=== RGB MASK ===")
    for name, p in [("ours_rgb", ours_rgb), ("comm_rgb", comm_rgb)]:
        with rasterio.open(p) as ds:
            alpha = ds.read(4)
            st = _mask_stats(alpha > 0)
            print(name, st)

    print("\n=== BLOB WINDOW ===")
    print("ours_rgb", _window_rgb(ours_rgb, BLOB_X, BLOB_Y))
    print("comm_rgb", _window_rgb(comm_rgb, BLOB_X, BLOB_Y))
    print("ours_dsm", _dsm_at(ours_dsm, BLOB_X, BLOB_Y))
    print("comm_dsm", _dsm_at(comm_dsm, BLOB_X, BLOB_Y))

    print("\n=== BLOB CC ===")
    with rasterio.open(ours_rgb) as ds:
        print("ours", _cc_at(ds.read(4) > 0, ds.transform, BLOB_X, BLOB_Y))
    with rasterio.open(comm_rgb) as ds:
        print("comm", _cc_at(ds.read(4) > 0, ds.transform, BLOB_X, BLOB_Y))

    print("\n=== HOLE vs DSM / TILE ===")
    print(_hole_vs_dsm(ours_rgb, ours_dsm))

    print("\n=== BOUNDS DELTA (ours - comm), meters ===")
    ob = _profile(ours_rgb)["bounds"]
    cb = _profile(comm_rgb)["bounds"]
    print(
        {
            "d_left": ob[0] - cb[0],
            "d_bottom": ob[1] - cb[1],
            "d_right": ob[2] - cb[2],
            "d_top": ob[3] - cb[3],
            "ours_w_m": ob[2] - ob[0],
            "ours_h_m": ob[3] - ob[1],
            "comm_w_m": cb[2] - cb[0],
            "comm_h_m": cb[3] - cb[1],
        }
    )


if __name__ == "__main__":
    main()
