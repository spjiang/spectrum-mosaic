"""地面格网。DSM、正射、拼接线、重叠度视图都在这套格网上对齐。

GSD 不写死某个测区。正射跟主相机原生像元（航高 / 焦距像素），DSM 为其 2 倍。
「正射 = DSM/2」是行业常规（Kraus；LiMapper 报告也写这一条），不是本测区常数。
对标已有成果时用 Grid.from_raster 锁参考 GeoTIFF，由调用方显式传入。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from rasterio.transform import Affine

# 正射 GSD 相对 DSM 的比例，与商业成品一致
ORTHO_OVER_DSM = 0.5


@dataclass(frozen=True)
class Grid:
    """北向朝上的规则格网。transform 为 rasterio 的像元左上角仿射。"""

    transform: Affine
    width: int
    height: int
    crs: str

    @property
    def gsd(self) -> float:
        return float(self.transform.a)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        left = self.transform.c
        top = self.transform.f
        return (left, top + self.height * self.transform.e, left + self.width * self.gsd, top)

    @classmethod
    def from_bounds(
        cls, bounds: tuple[float, float, float, float], gsd: float, crs: str, *, snap: bool = True
    ) -> Grid:
        left, bottom, right, top = bounds
        if snap:
            # 把原点对齐到 GSD 的整数倍，便于与其他成果套合
            left = math.floor(left / gsd) * gsd
            bottom = math.floor(bottom / gsd) * gsd
            right = math.ceil(right / gsd) * gsd
            top = math.ceil(top / gsd) * gsd
        width = max(1, int(round((right - left) / gsd)))
        height = max(1, int(round((top - bottom) / gsd)))
        return cls(Affine(gsd, 0.0, left, 0.0, -gsd, top), width, height, crs)

    @classmethod
    def from_raster(cls, path) -> Grid:
        """直接采用已有 GeoTIFF 的变换、尺寸、坐标系。

        商业 DSM / 正射的原点并不是 GSD 的整数倍，正射相对 DSM 还偏了半个
        正射像元。若再用 from_bounds 取整，像元中心会对不齐，QGIS 里无法
        「每个像元」对比。
        """
        import rasterio

        with rasterio.open(path) as ds:
            return cls(ds.transform, ds.width, ds.height, str(ds.crs))

    def refine(self, factor: float = ORTHO_OVER_DSM) -> Grid:
        """按比例细化（默认 DSM → 正射，GSD 减半，范围不变）。"""
        left, bottom, right, top = self.bounds
        gsd = self.gsd * factor
        width = int(round((right - left) / gsd))
        height = int(round((top - bottom) / gsd))
        return Grid(Affine(gsd, 0.0, left, 0.0, -gsd, top), width, height, self.crs)

    def cell_centers(self, window: tuple[int, int, int, int] | None = None):
        """返回格网（或子窗口）像元中心的 X、Y 坐标数组，形状同窗口。"""
        row0, col0, rows, cols = window or (0, 0, self.height, self.width)
        cols_idx = np.arange(col0, col0 + cols) + 0.5
        rows_idx = np.arange(row0, row0 + rows) + 0.5
        xs = self.transform.c + cols_idx * self.transform.a
        ys = self.transform.f + rows_idx * self.transform.e
        return np.meshgrid(xs, ys)

    def tiles(self, tile: int, overlap: int = 0):
        """切块遍历，返回 (row0, col0, rows, cols)。overlap 用于窗口算子的边界。"""
        for row0 in range(0, self.height, tile):
            for col0 in range(0, self.width, tile):
                r0 = max(0, row0 - overlap)
                c0 = max(0, col0 - overlap)
                r1 = min(self.height, row0 + tile + overlap)
                c1 = min(self.width, col0 + tile + overlap)
                yield (r0, c0, r1 - r0, c1 - c0)


def ground_reference_z(points: np.ndarray, *, cell_m: float = 10.0) -> float:
    """定 GSD 与足迹用的参考地面高程：按面积加权的稳健地形面。

    直接取 median(稀疏点 z) 不行，有两个毛病：
    - 点的空间密度不均。纹理好的缓坡出点多、林冠与陡坡出点少，分位数是按
      「点数」而不是按「面积」算的，本测区实测比商业等价参考面低 34 m，
      GSD 因此偏大 27%、足迹也跟着涨大。
    - 空三点里出现过 −101 km 的飞点。

    这里先把点落到 cell_m 的粗格网取中值（一格一票，消掉密度权重，也压掉
    飞点），再对占用格网取均值 —— 即「块平均地面高程」，这正是航空摄影测量
    里算平均 GSD 用的参考面（Kraus《Photogrammetry》）。

    cell_m=10 m 实测：参考面 1725.1 m，与商业 DSM 足迹内的面均高 1730.9 m
    只差 5.8 m（稀疏点普遍落在冠层之下，偏低是应该的）；cell=20 m 给出同样的
    结果，说明这个尺度已经稳定。相比之下点数中位数给 1691.3 m，差 39.6 m。
    """
    pts = np.asarray(points, float)
    if pts.ndim != 2 or pts.shape[0] == 0:
        raise ValueError("没有稀疏点，无法定参考地面高程")
    z = pts[:, 2]
    ok = np.isfinite(pts).all(axis=1)
    if not ok.any():
        raise ValueError("稀疏点全部非有限值")
    pts, z = pts[ok], z[ok]
    # 先按 MAD 去飞点，避免 −101 km 那类点把粗格网中值带跑
    med = float(np.median(z))
    mad = 1.4826 * float(np.median(np.abs(z - med)))
    keep = np.abs(z - med) <= 8.0 * max(mad, 1.0)
    pts, z = pts[keep], z[keep]
    if z.size == 0:
        return med
    cell = max(float(cell_m), 1e-3)
    ci = np.floor(pts[:, 0] / cell).astype(np.int64)
    ri = np.floor(pts[:, 1] / cell).astype(np.int64)
    key = (ri - ri.min()).astype(np.int64) * (int(ci.max() - ci.min()) + 1) + (ci - ci.min())
    order = np.argsort(key, kind="stable")
    key_s, z_s = key[order], z[order]
    _, start = np.unique(key_s, return_index=True)
    bounds = list(start) + [z_s.size]
    cell_med = np.array([np.median(z_s[a:b]) for a, b in zip(bounds[:-1], bounds[1:])])
    return float(np.mean(cell_med))


def estimate_native_gsd(camera, poses: dict, ground_z: float) -> float:
    """主相机一个像元对应的地面尺寸：GSD = H / f_px（Kraus《Photogrammetry》）。"""
    agls = [float(pose.center[2] - ground_z) for pose in poses.values()]
    agls = [h for h in agls if h > 1.0]
    height = float(np.median(agls)) if agls else 100.0
    focal = max(float(camera.f), 1.0)
    return height / focal


def estimate_dsm_gsd(camera, poses: dict, ground_z: float) -> float:
    """DSM 取原生 GSD 的 2 倍，正射再 refine 回一半。"""
    return 2.0 * estimate_native_gsd(camera, poses, ground_z)


def estimate_z_margin_m(points: np.ndarray) -> float:
    """高程搜索半宽跟稀疏点起伏走，不写死 12/24 m。

    先验面平滑后相对树冠/陡坎仍可能偏「起伏的一小截」。取 p90−p10 的 1/4，
    夹在 8–48 m，兼顾平地与本测区那种百米级起伏。
    """
    if points is None or len(points) < 16:
        return 16.0
    z = np.asarray(points[:, 2], float)
    z = z[np.isfinite(z)]
    if z.size < 16:
        return 16.0
    span = float(np.percentile(z, 90) - np.percentile(z, 10))
    return float(np.clip(max(8.0, 0.25 * span), 8.0, 48.0))


def grid_from_points(
    points: np.ndarray, gsd: float, crs: str, *, percentile: float = 0.5, pad_m: float = 0.0
) -> Grid:
    """由稀疏点云范围定格网。用分位数裁掉零星飞点，避免格网被拉得过大。"""
    lo = np.percentile(points[:, :2], percentile, axis=0)
    hi = np.percentile(points[:, :2], 100.0 - percentile, axis=0)
    return Grid.from_bounds(
        (lo[0] - pad_m, lo[1] - pad_m, hi[0] + pad_m, hi[1] + pad_m), gsd, crs
    )


def grid_from_footprints(
    cameras: dict, poses: dict, ground_z: float, gsd: float, crs: str, *, pad_m: float = 10.0
) -> Grid:
    """由影像地面足迹并集定格网，对齐商业软件「按航摄覆盖铺画幅」的做法。

    用空三稀疏点的分位数 AABB 会把条带两端裁掉，画幅几乎裁成正方形，QGIS
    里看起来就是一块矩形；商业成品把格网铺到足迹外缘，四周 alpha=0，轮廓
    才是航线形状。
    """
    from shapely.ops import unary_union

    from ms_mosaic.pairs import footprint_polygons

    polys = footprint_polygons(cameras, poses, ground_z)
    if not polys:
        raise ValueError("没有有效足迹，无法定格网")
    geom = unary_union(list(polys.values()))
    if pad_m:
        geom = geom.buffer(float(pad_m))
    minx, miny, maxx, maxy = geom.bounds
    return Grid.from_bounds((minx, miny, maxx, maxy), gsd, crs)


def coverage_from_footprints(
    grid: Grid, cameras: dict, poses: dict, ground_z: float
) -> np.ndarray:
    """把相片地面足迹栅格化到 DSM 格网。真正射覆盖的上界是足迹并集，不是立体匹配成功的格网。"""
    from rasterio.features import rasterize

    from ms_mosaic.pairs import footprint_polygons

    polys = footprint_polygons(cameras, poses, ground_z)
    if not polys:
        return np.zeros(grid.shape, bool)
    mask = rasterize(
        [(geom, 1) for geom in polys.values()],
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    )
    return mask.astype(bool)


def coverage_from_product(path, grid: Grid) -> np.ndarray:
    """把参考成果有效掩膜重采样到给定格网。只给比对报告用，不进交付覆盖。"""
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(path) as src:
        if src.count >= 4:
            arr = (src.read(src.count) > 0).astype(np.uint8)
        else:
            z = src.read(1)
            nodata = src.nodata
            ok = np.isfinite(z)
            if nodata is not None:
                ok &= z != nodata
            if np.issubdtype(z.dtype, np.floating):
                ok &= z > -1.0e6
            arr = ok.astype(np.uint8)
        if (
            src.width == grid.width
            and src.height == grid.height
            and src.transform == grid.transform
        ):
            return arr.astype(bool)
        out = np.zeros(grid.shape, np.uint8)
        reproject(
            arr,
            out,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            resampling=Resampling.nearest,
        )
        return out.astype(bool)


def erode_coverage(mask: np.ndarray, gsd: float, trim_m: float) -> np.ndarray:
    """从外轮廓往里收 trim_m 米。先填内部孔，禁止从孔往外扩。

    4 连通腐蚀若带着内部孔一起做，每个小孔都会扩成菱形。本测区曾因此在
    林内打出满图白洞。商业正射是单一连通域、几乎无孔，收边只削最外一圈
    单视斜视。换测区只改模版里的 edge_trim_m。
    """
    from scipy.ndimage import binary_erosion, binary_fill_holes, generate_binary_structure

    take = binary_fill_holes(np.asarray(mask, bool))
    n = int(round(float(trim_m) / max(float(gsd), 1e-9)))
    if n <= 0:
        return take
    return binary_erosion(take, structure=generate_binary_structure(2, 1), iterations=n)


def smooth_coverage_mask(mask: np.ndarray, gsd: float, *, closing_m: float = 2.0) -> np.ndarray:
    """闭运算修边：商业正射外轮廓接近足迹并集的光滑包络，不是立体匹配的锯齿。"""
    from scipy.ndimage import binary_closing, binary_fill_holes, generate_binary_structure

    out = binary_fill_holes(np.asarray(mask, bool))
    n = max(1, int(round(float(closing_m) / max(float(gsd), 1e-9))))
    return binary_closing(out, structure=generate_binary_structure(2, 1), iterations=n)


def refine_coverage_mask(
    valid: np.ndarray,
    *,
    max_hole_cells: int = 256,
    merge_gap_cells: int = 8,
    min_island_cells: int = 256,
) -> np.ndarray:
    """去掉小游离斑、填掉内部小孔，够大的另一块覆盖要留下。

    商业正射 alpha 只有 1 个连通域、几乎无孔。自研全量成果曾出现：
    - 十万级内部空洞（QGIS 白点）：正射在 DSM 有效处仍留下 nan
    - 贴着主体、只隔 1～数像素的碎块：必须并回去，否则会切掉商业也有的条带边缘
    - 只有几像素到几十像素、离主体很远的游离斑（右下角绿斑）：商业 alpha=0，必须丢掉

    不能把「不与最大块相连」一律删掉。红树林航带被水道隔开，立体匹配是多块树冠：
    粗格网在足迹里大约解出 15%，最大连通域只占约 2%，其余东边解出的部分
    与主体隔了不止几个像素。按面积留下这些块，小绿斑仍低于 min_island_cells 被丢掉。
    """
    from scipy.ndimage import (
        binary_closing,
        binary_dilation,
        binary_fill_holes,
        generate_binary_structure,
        label,
    )

    valid = np.asarray(valid, bool)
    lab, n = label(valid)
    if n == 0:
        return valid
    sizes = np.bincount(lab.ravel())
    main_id = int(np.argmax(sizes[1:]) + 1)
    main = lab == main_id
    if merge_gap_cells > 0 and n > 1:
        conn = generate_binary_structure(2, 2)
        gap = int(merge_gap_cells)
        touch = binary_dilation(main, structure=conn, iterations=gap)
        ids = np.unique(lab[touch])
        ids = ids[ids > 0]
        # 面积够的连通域全部保留，不论是否贴着最大块。贴着主体的小碎块仍按原规则丢掉。
        # sizes[0] 是背景像元个数，不能当成一块覆盖。
        big = np.flatnonzero(sizes[1:] >= int(min_island_cells)) + 1
        if big.size:
            ids = np.unique(np.concatenate([ids.astype(np.int64), big.astype(np.int64)]))
        keep = (ids == main_id) | (sizes[ids] >= int(min_island_cells))
        take = np.isin(lab, ids[keep])
        take = binary_closing(take, structure=conn, iterations=min(gap, 3))
        main = take
    if max_hole_cells > 0:
        filled = binary_fill_holes(main)
        holes = filled & ~main
        if holes.any():
            hlab, nh = label(holes)
            if nh:
                hs = np.bincount(hlab.ravel())
                small = (np.arange(hs.size) > 0) & (hs <= int(max_hole_cells))
                main = main | np.isin(hlab, np.flatnonzero(small))
    lab3, n3 = label(main)
    if n3 <= 1:
        return main
    sizes3 = np.bincount(lab3.ravel())
    keep3 = (np.arange(sizes3.size) == int(np.argmax(sizes3[1:]) + 1)) | (
        (np.arange(sizes3.size) > 0) & (sizes3 >= int(min_island_cells))
    )
    return np.isin(lab3, np.flatnonzero(keep3))


def inpaint_nearest(data: np.ndarray, known: np.ndarray, take: np.ndarray) -> np.ndarray:
    """把 take 且未知的像元，用最近的 known 像元填上。data 为 (h,w) 或 (c,h,w)。"""
    from scipy.ndimage import distance_transform_edt

    known = np.asarray(known, bool)
    take = np.asarray(take, bool)
    need = take & ~known
    out = np.array(data, copy=True)
    if not need.any() or not known.any():
        return out
    _, idx = distance_transform_edt(~known, return_indices=True)
    rr, cc = idx[0][need], idx[1][need]
    if out.ndim == 2:
        out[need] = out[rr, cc]
    else:
        for b in range(out.shape[0]):
            plane = out[b]
            plane[need] = plane[rr, cc]
            out[b] = plane
    return out
