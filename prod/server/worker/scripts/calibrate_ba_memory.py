"""标定分波段平差内存模型的系数（band_worker_bytes）。

在合成的两波段问题上用 tracemalloc 量单路「组装 + Schur 消元」的峰值，
按观测数、物方点数、分块大小变化拟合每项系数。numpy/scipy 的数组内存
都会报给 tracemalloc，所以这里量到的就是工人线程真正多占的部分。

    python scripts/calibrate_ba_memory.py
"""

from __future__ import annotations

import gc
import tracemalloc

import numpy as np

from ms_mosaic import at as at_mod
from ms_mosaic.at import BAProblem, Observation, _assemble_band, _damp_solve_band, _separable_bands
from ms_mosaic.camera import Camera, Pose

NADIR = np.diag([1.0, -1.0, -1.0])


def synthetic(n_img: int, n_pts: int, views: int, seed: int = 0) -> BAProblem:
    rng = np.random.default_rng(seed)
    cameras = {
        "Color": Camera.initial("Color", 2048, 1536, kind="rgb"),
        "450": Camera.initial("450", 2048, 1536, kind="ms"),
    }
    poses, gps, att, image_camera = {}, {}, {}, {}
    side = int(np.ceil(np.sqrt(n_img)))
    for band_no, key in enumerate(("Color", "450")):
        for k in range(n_img):
            idx = band_no * 10_000 + k
            c = np.array([(k % side) * 10.0, (k // side) * 10.0, 100.0])
            poses[idx] = Pose(NADIR, c)
            gps[idx] = c.copy()
            att[idx] = NADIR
            image_camera[idx] = key
    points, observations = [], []
    for band_no in range(2):
        ids = [band_no * 10_000 + k for k in range(n_img)]
        for _ in range(n_pts):
            p = np.array([rng.uniform(0, side * 10), rng.uniform(0, side * 10), rng.uniform(-2, 2)])
            pid = len(points)
            points.append(p)
            for img in rng.choice(ids, size=views, replace=False):
                observations.append(Observation(int(img), pid, rng.uniform(0, 2048, 2)))
    return BAProblem(
        cameras,
        poses,
        np.array(points),
        observations,
        image_camera,
        gps,
        att,
        free_names=at_mod.CALIBRATION_STAGES[-1],
    )


def peak(problem: BAProblem, band, chunk: int) -> int:
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    assembled = _assemble_band(problem, band, problem.x0, at_mod.HUBER_SCALE_PX, chunk=chunk)
    solved = _damp_solve_band(assembled, 1e-3)
    _, top = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del assembled, solved
    return top


def main() -> None:
    rows = []
    for n_img, n_pts, views, chunk in [
        (60, 20_000, 3, 10**9),
        (60, 40_000, 3, 10**9),
        (60, 80_000, 3, 10**9),
        (60, 80_000, 3, 40_000),
        (60, 80_000, 3, 80_000),
        (120, 80_000, 4, 50_000),
        (200, 60_000, 4, 30_000),
    ]:
        problem = synthetic(n_img, n_pts, views)
        band = _separable_bands(problem)[0]
        n_obs = int(band.obs_idx.size)
        top = peak(problem, band, chunk)
        rows.append((n_obs, band.n_pts, band.nc, problem.n_free, min(n_obs, chunk), top))
        est = at_mod.band_worker_bytes(n_obs, band.n_pts, band.nc, problem.n_free, chunk=chunk)
        print(
            f"obs={n_obs:>8} pts={band.n_pts:>7} nc={band.nc:>5} chunk={min(n_obs, chunk):>8} "
            f"peak={top / 2**20:8.1f} MB  model={est / 2**20:8.1f} MB"
        )
    # 拟合 peak ≈ a·chunk_obs·width/16 + b·n_obs + c·n_pts + 8·nc²·copies（不含安全系数）
    a_rows, y = [], []
    for n_obs, n_pts, nc, n_free, chunk_obs, top in rows:
        width = 6 + n_free
        a_rows.append([chunk_obs * width / 16, n_obs, n_pts])
        y.append(top - nc * nc * 8 * at_mod._BA_DENSE_COPIES)
    coef, *_ = np.linalg.lstsq(np.array(a_rows, float), np.array(y, float), rcond=None)
    print("fit bytes/chunk_obs, bytes/obs, bytes/point:", np.round(coef, 1))


if __name__ == "__main__":
    main()
