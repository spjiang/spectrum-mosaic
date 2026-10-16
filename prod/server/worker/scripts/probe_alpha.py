"""核对正射「黑底」：是 alpha 无效区被预览成黑，还是有效区内真有黑斑。

商业成品同样是整幅画布 + alpha=0 表示测区外。预览器如果不读第四波段，
两边都会是黑底。真正的毛病是 alpha>0 但 RGB 全 0（有效区内的黑斑），
或 alpha 波段丢了（整幅被当成不透明黑）。
同时量商业轮廓相对足迹并集往里收了多少米，作为通用裁边参数，
而不是每次去抄商业掩膜。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.grid import Grid, coverage_from_footprints, ground_reference_z, smooth_coverage_mask
from ms_mosaic.pairs import footprint_polygons

REF = Path("/data/input/MAX_20251017/拼图结果/Orthomosaic_pix_surf_group0.tif")
CACHE = Path("/data/output/runs/demo_max_20251017_rgb/20260920_205435/cache/at_result.npz")
_CAM = ("key", "width", "height", "f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2", "model")


def report(name: str, path: Path) -> None:
    with rasterio.open(path) as ds:
        rgb = np.stack([ds.read(i + 1) for i in range(min(3, ds.count))])
        alpha = ds.read(4) if ds.count >= 4 else None
        print(f"{name}: 波段={ds.count} dtype={ds.dtypes} nodata={ds.nodata} "
              f"colorinterp={list(ds.colorinterp)} photometric={ds.photometric}")
    if alpha is None:
        print("  没有 alpha 波段")
        return
    ok = alpha > 0
    black = ok & (rgb[0] == 0) & (rgb[1] == 0) & (rgb[2] == 0)
    print(f"  alpha>0={ok.mean():.4f}  有效区内RGB全0={black.mean():.4%} "
          f"({int(black.sum())}格)  alpha唯一值={np.unique(alpha)[:6]}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ours", type=Path)
    args = ap.parse_args()
    ours = args.ours / "Orthomosaic_pix_surf_group0.tif" if args.ours.is_dir() else args.ours
    report("自研", ours)
    report("商业", REF)

    with rasterio.open(REF) as ds:
        grid = Grid(ds.transform, ds.width, ds.height, str(ds.crs))
        ca = ds.read(4) > 0
    with np.load(CACHE, allow_pickle=False) as d:
        meta = json.loads(bytes(d["meta"]).decode("utf-8"))
        ids = [int(i) for i in d["pose_ids"]]
        pr, pc = np.asarray(d["pose_R"], float), np.asarray(d["pose_C"], float)
        pts = np.asarray(d["points"], float)
    poses = {i: Pose(rotation=pr[k], center=pc[k]) for k, i in enumerate(ids)}
    raw = {c["key"]: c for c in meta["cameras"]}
    cam = Camera(**{k: raw[meta.get("camera_key") or "Color"][k] for k in _CAM})
    cams = {i: cam for i in poses}
    gz = ground_reference_z(pts)
    foot = smooth_coverage_mask(coverage_from_footprints(grid, cams, poses, gz), grid.gsd)
    dist = distance_transform_edt(foot) * grid.gsd
    # 商业有效格到足迹外缘的距离
    inside = dist[ca]
    print(f"\n足迹覆盖={foot.mean():.4f} 商业={ca.mean():.4f}")
    print(f"商业有效格距足迹外缘 p05/p50/p10 = "
          f"{np.percentile(inside,5):.1f}/{np.percentile(inside,50):.1f}/{np.percentile(inside,10):.1f} m")
    # 足迹有、商业无：这圈就是商业裁掉的宽度
    extra = foot & ~ca
    if extra.any():
        print(f"足迹多出、商业裁掉的一圈：占画布 {extra.mean():.4f}  "
              f"宽度(到足迹外缘) p50/p90={np.percentile(dist[extra],[50,90]).round(1)} m")
        # 这圈到商业外缘（即商业往里收的距离）
        din = distance_transform_edt(~ca) * grid.gsd
        print(f"  相对商业轮廓往外伸 p50/p90={np.percentile(din[extra],[50,90]).round(1)} m")


if __name__ == "__main__":
    main()
