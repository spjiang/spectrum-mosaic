"""边缘窗：最正下视的离轴角，以及地面 1 m 在像方拉开的长短轴。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio

from ms_mosaic.camera import project
from ms_mosaic.checkpoint import load_at_payload
from ms_mosaic.dense import off_nadir_deg

AT = Path("/data/output/runs/demo_max_20251017_rgb/20260920_205435/cache/at_result.npz")
DSM = Path("/data/output/runs/demo_max_20251017_restore/拼图结果/DSM.tif")
COMM = Path("/data/input/MAX_20251017/拼图结果/DSM.tif")
WINDOWS = (
    ("右上北", 674607.2, 2620470.2),
    ("右上南", 674601.6, 2620222.0),
    ("东缘", 674560.0, 2620230.0),
    ("北缘", 674260.0, 2620500.0),
    ("内部", 674250.0, 2620200.0),
)


def sample_z(path: Path, x: float, y: float) -> float:
    with rasterio.open(path) as ds:
        row, col = ds.index(x, y)
        if not (0 <= row < ds.height and 0 <= col < ds.width):
            return float("nan")
        v = float(ds.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
        if ds.nodata is not None and v == ds.nodata:
            return float("nan")
        if v < -1e6:
            return float("nan")
        return v


def anisotropy(cam, pose, x: float, y: float, z: float) -> tuple[float, float]:
    """地面 ±0.5 m 十字在像方的长短轴（像素）。接近 1:1 才不拉丝。"""
    pts = np.array(
        [
            [x, y, z],
            [x + 0.5, y, z],
            [x - 0.5, y, z],
            [x, y + 0.5, z],
            [x, y - 0.5, z],
        ],
        float,
    )
    u, v, ok = project(cam, pose, pts)
    if not ok.all():
        return float("nan"), float("nan")
    du = np.hypot(u[1] - u[2], v[1] - v[2])
    dv = np.hypot(u[3] - u[4], v[3] - v[4])
    axes = sorted((float(du), float(dv)))
    return axes[1], axes[1] / max(axes[0], 1e-6)


def dump_one(cam, pose, x: float, y: float, z: float, idx: int) -> None:
    pts = np.array(
        [[x, y, z], [x + 0.5, y, z], [x, y + 0.5, z]],
        float,
    )
    pc = pose.world_to_camera(pts)
    u, v, ok = project(cam, pose, pts)
    print(
        f"  dump id={idx} f={cam.f:.1f} C=({pose.center[0]:.1f},{pose.center[1]:.1f},{pose.center[2]:.1f}) "
        f"axis_z={pose.viewing_direction[2]:.3f}",
        flush=True,
    )
    for i, tag in enumerate(("c", "e", "n")):
        print(
            f"    {tag} cam=({pc[i,0]:.2f},{pc[i,1]:.2f},{pc[i,2]:.2f}) "
            f"uv=({u[i]:.1f},{v[i]:.1f}) ok={bool(ok[i])}",
            flush=True,
        )


def main() -> None:
    payload = load_at_payload(AT)
    poses = payload["poses"]
    cams = payload["camera_objs"]
    cam = next(iter(cams.values()))
    print(f"poses={len(poses)} cameras={list(cams)}", flush=True)
    tilts = np.array([off_nadir_deg(p) for p in poses.values()])
    print(
        f"tilt p10/p50/p90={np.percentile(tilts, [10, 50, 90])} max={tilts.max():.1f}",
        flush=True,
    )
    for name, x, y in WINDOWS:
        zo = sample_z(DSM, x, y)
        zc = sample_z(COMM, x, y)
        hits = []
        for idx, pose in poses.items():
            tilt = off_nadir_deg(pose)
            if tilt > 60:
                continue
            u, v, ok = project(cam, pose, np.array([[x, y, zo if np.isfinite(zo) else zc]], float))
            if not (ok[0] and cam.in_bounds(u, v, margin=0)[0]):
                continue
            hits.append((tilt, idx, pose))
        z_use = zo if np.isfinite(zo) else zc
        rows = []
        for tilt, idx, pose in hits:
            long_o, ratio_o = anisotropy(cam, pose, x, y, z_use)
            depth = float(pose.center[2] - z_use)
            rows.append((ratio_o, long_o, tilt, depth, idx))
        rows.sort()
        good = [r for r in rows if r[0] < 1.8 and 8.0 <= r[1] <= 40.0]
        print(
            f"\n{name} z_ours={zo:.1f} z_comm={zc:.1f} views={len(hits)} "
            f"healthy={len(good)}",
            flush=True,
        )
        show = rows[:2] + rows[-2:]
        for ratio, long_px, tilt, depth, idx in show:
            pose = poses[idx]
            u, v, ok = project(cam, pose, np.array([[x, y, z_use]], float))
            du = (float(u[0]) - cam.cx) / (0.5 * cam.width)
            dv = (float(v[0]) - cam.cy) / (0.5 * cam.height)
            print(
                f"  id={idx} uv=({float(u[0]):.0f},{float(v[0]):.0f}) "
                f"radial={np.hypot(du, dv):.2f} size={cam.width}x{cam.height} "
                f"tilt={tilt:.1f}° 1m→{long_px:.1f}px x{ratio:.2f}",
                flush=True,
            )
        for ratio, long_px, tilt, depth, idx in rows[:4]:
            print(
                f"  best-geom id={idx} tilt={tilt:.1f}° depth={depth:.1f}m "
                f"1m→{long_px:.1f}px x{ratio:.2f}",
                flush=True,
            )
        worst = rows[-1]
        if name in ("右上南", "东缘", "内部"):
            dump_one(cam, poses[rows[0][4]], x, y, z_use, rows[0][4])
            dump_one(cam, poses[worst[4]], x, y, z_use, worst[4])
        print(
            f"  worst id={worst[4]} tilt={worst[2]:.1f}° depth={worst[3]:.1f}m "
            f"1m→{worst[1]:.1f}px x{worst[0]:.2f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
