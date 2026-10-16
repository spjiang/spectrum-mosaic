"""真正射：以 DSM 为高程面逐像元反算，并用 Z-buffer 剔除遮挡。

为什么必须是「真」正射
--------------------
本批数据 AGL 只有约 111 m，而测区地形起伏 161 m。若按单一水平面投影，
边缘像元的平面位移可达 起伏 × tan(视角)，量级几十米 —— 这正是早期版本
拼图「完全不对、有重叠」的根因。以 DSM 逐像元定高程才能消掉这个位移。

遮挡检测
--------
2.5D 高程面下，标准做法是对每张影像做一次 Z-buffer：把所有正射格网点投到
该影像，按到摄站的距离取最近者；某格网点的距离明显大于同一像素上的最近距离，
说明它被前方地物挡住，该视角对它无效。相比沿视线逐步爬升的射线法，
Z-buffer 一次投影就能判完整幅，代价与格网规模成线性。

本模块只负责「把每个视角重采样到正射格网 + 判可见性 + 给视角质量分」，
选哪个视角、怎么融合分别交给 seamline 与 blend。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ms_mosaic.camera import Camera, Pose, project
from ms_mosaic.dense import _sample_bilinear, off_nadir_deg
from ms_mosaic.grid import Grid

OCCLUSION_TOLERANCE_M = 0.6
MAX_VIEWS_PER_TILE = 12
MAX_TILT_DEG = 60.0
EDGE_MARGIN_PX = 0
FACADE_MAX_SAMPLES = 64


@dataclass
class OrthoConfig:
    occlusion_tolerance_m: float = OCCLUSION_TOLERANCE_M
    max_views: int = MAX_VIEWS_PER_TILE
    max_tilt_deg: float = MAX_TILT_DEG
    edge_margin_px: int = EDGE_MARGIN_PX
    facade_max_samples: int = FACADE_MAX_SAMPLES


@dataclass
class WarpedStack:
    """一个正射格网块上，各视角重采样后的影像与质量分。"""

    window: tuple[int, int, int, int]
    views: list[int]
    pixels: np.ndarray  # (n_views, bands, h, w)，nan 表示该视角在此无效
    scores: np.ndarray  # (n_views, h, w)，越大越优先

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.scores)

    @property
    def n_bands(self) -> int:
        return self.pixels.shape[1]


class NativeImageCache:
    """读取原始位深、原始波段数的影像。RGB 为 3 波段 uint8，多光谱为单波段 uint16。"""

    def __init__(self, paths: dict[int, Path], *, limit: int = 24) -> None:
        self._paths = paths
        self._cache: dict[int, np.ndarray] = {}
        self._order: list[int] = []
        self._limit = limit

    def __getitem__(self, index: int) -> np.ndarray:
        hit = self._cache.get(index)
        if hit is not None:
            return hit
        from ms_mosaic.rawio import read_native

        arr = np.asarray(read_native(self._paths[index]))
        if arr.ndim == 2:
            arr = arr[None, :, :]
        else:
            arr = np.moveaxis(arr, -1, 0)
        arr = arr.astype(np.float32)
        self._cache[index] = arr
        self._order.append(index)
        while len(self._order) > self._limit:
            self._cache.pop(self._order.pop(0), None)
        return arr


def view_score(
    camera: Camera, pose: Pose, pts: np.ndarray, u: np.ndarray, v: np.ndarray
) -> np.ndarray:
    """视角质量分：正下视且靠近像幅中心者优先。

    两项都有实际依据：入射角越正，地物侧面遮挡与投影拉伸越小；离像幅中心越近，
    畸变残差与边缘暗角越小。乘积形式让任一项很差时整体就被压低。
    """
    los = pts - pose.center
    dist = np.linalg.norm(los, axis=1)
    # 入射角余弦（视线与地表法向的夹角，地表法向近似取垂直向上）
    cos_inc = np.clip(-los[:, 2] / np.maximum(dist, 1e-9), 0.0, 1.0)
    du = (u - camera.cx) / (0.5 * camera.width)
    dv = (v - camera.cy) / (0.5 * camera.height)
    radial = np.sqrt(du * du + dv * dv)
    center_weight = np.clip(1.0 - 0.5 * radial, 0.0, 1.0)
    return (cos_inc**2) * center_weight


def _facade_samples(
    pts: np.ndarray, drop_m: np.ndarray, *, gsd: float, max_sub: int, min_drop_m: float
) -> np.ndarray:
    """在高程跳变处补出竖向立面的采样点。

    2.5D 格网只采到地物顶面，建筑立面在格网里是相邻两格之间的一个断点，
    不对应任何采样点。于是立面遮住的那片地面在 Z-buffer 里没有竞争者，
    遮挡就会漏判 —— 一堵 25 m 高的墙在 111 m 航高下能遮住像方约 30 个像素的
    地面，而 DSM 在跳变处只有一两个格网。这里把立面泼成采样点补上。

    采样间距要和地面 GSD 同量级，相邻两个立面采样才会落在相邻像素上。
    固定段数不行：25 m 的立面切 12 段间距 2.08 m，在像方留下约 3 px 的缝，
    实测仍有 28% 的阴影地面从缝里漏判为可见。
    """
    tall = drop_m > min_drop_m
    if not tall.any():
        return np.zeros((0, 3))
    base = pts[tall]
    depth = drop_m[tall]
    n_sub = int(np.clip(np.ceil(float(depth.max()) / max(gsd, 1e-6)), 1, max_sub))
    # 从顶面往下均匀取 n_sub 层，不含顶面本身（顶面已在主采样里）
    fracs = (np.arange(1, n_sub + 1) / n_sub)[:, None]
    extra = np.repeat(base[None, :, :], n_sub, axis=0)
    extra[:, :, 2] -= fracs * depth[None, :]
    return extra.reshape(-1, 3)


def visibility_zbuffer(
    camera: Camera,
    pose: Pose,
    pts: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    inside: np.ndarray,
    *,
    tolerance_m: float,
    drop_m: np.ndarray | None = None,
    gsd: float = 0.1,
    max_sub: int = FACADE_MAX_SAMPLES,
    min_drop_m: float = 0.5,
) -> np.ndarray:
    """Z-buffer 遮挡判定。返回每个格网点在该视角下是否可见。

    把格网点按整数像素归并，每个像素只留距摄站最近的那个；某点的距离超出
    该像素最近距离加容差，即判为被前方地物挡住。drop_m 给出每个格网点相对
    邻域最低处的高差，用来补出立面采样点（见 _facade_samples）。
    """
    visible = np.zeros(pts.shape[0], bool)
    if not inside.any():
        return visible
    sel = np.nonzero(inside)[0]
    main = pts[sel]
    rng = np.linalg.norm(main - pose.center, axis=1)
    cols = np.clip(np.round(u[sel]).astype(np.int64), 0, camera.width - 1)
    rows = np.clip(np.round(v[sel]).astype(np.int64), 0, camera.height - 1)
    flat = rows * camera.width + cols

    occluders = [(flat, rng)]
    if drop_m is not None:
        extra = _facade_samples(
            main, np.asarray(drop_m)[sel], gsd=gsd, max_sub=max_sub, min_drop_m=min_drop_m
        )
        if extra.size:
            eu, ev, eok = project(camera, pose, extra)
            keep = eok & camera.in_bounds(eu, ev, margin=0)
            if keep.any():
                ec = np.clip(np.round(eu[keep]).astype(np.int64), 0, camera.width - 1)
                er = np.clip(np.round(ev[keep]).astype(np.int64), 0, camera.height - 1)
                occluders.append(
                    (
                        er * camera.width + ec,
                        np.linalg.norm(extra[keep] - pose.center, axis=1),
                    )
                )

    all_flat = np.concatenate([f for f, _ in occluders])
    all_rng = np.concatenate([r for _, r in occluders])
    # 按距离降序写入，同一像素上最后落笔的就是最近点 —— 比 minimum.at 快得多
    order = np.argsort(-all_rng, kind="stable")
    zbuf = np.full(camera.width * camera.height, np.inf, np.float64)
    zbuf[all_flat[order]] = all_rng[order]

    visible[sel] = rng <= zbuf[flat] + tolerance_m
    return visible


def neighbor_drop(z: np.ndarray) -> np.ndarray:
    """每个格网相对 4 邻域最低处的高差，用于识别立面所在的位置。"""
    from scipy.ndimage import minimum_filter

    proxy = np.where(np.isfinite(z), z, np.inf)
    lowest = minimum_filter(proxy, footprint=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]),
                            mode="nearest")
    drop = np.where(np.isfinite(z) & np.isfinite(lowest), z - lowest, 0.0)
    return np.maximum(drop, 0.0)


def select_views(
    grid: Grid,
    window: tuple[int, int, int, int],
    z_mid: float,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    cfg: OrthoConfig,
) -> list[int]:
    """挑出覆盖该块的影像，正下视优先。"""
    row0, col0, rows, cols = window
    xs, ys = grid.cell_centers(window)
    probe = np.stack(
        [
            np.array([xs[0, 0], xs[0, -1], xs[-1, 0], xs[-1, -1], xs[rows // 2, cols // 2]]),
            np.array([ys[0, 0], ys[0, -1], ys[-1, 0], ys[-1, -1], ys[rows // 2, cols // 2]]),
            np.full(5, z_mid),
        ],
        axis=1,
    )
    scored = []
    for idx, pose in poses.items():
        tilt = off_nadir_deg(pose)
        if tilt > cfg.max_tilt_deg:
            continue
        cam = cameras[idx]
        u, v, ok = project(cam, pose, probe)
        hit = ok & cam.in_bounds(u, v, margin=cfg.edge_margin_px)
        if not hit.any():
            continue
        scored.append((-int(hit.sum()), tilt, idx))
    scored.sort()
    return [idx for _, _, idx in scored[: cfg.max_views]]


def orthorectify_tile(
    grid: Grid,
    window: tuple[int, int, int, int],
    z: np.ndarray,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    images: NativeImageCache,
    cfg: OrthoConfig | None = None,
) -> WarpedStack:
    """把覆盖该块的每个视角都重采样到正射格网上。z 为该块的 DSM 高程。"""
    cfg = cfg or OrthoConfig()
    row0, col0, rows, cols = window
    xs, ys = grid.cell_centers(window)
    has_z = np.isfinite(z)
    z_mid = float(np.nanmedian(z)) if has_z.any() else 0.0

    views = select_views(grid, window, z_mid, cameras, poses, cfg)
    if not views or not has_z.any():
        return WarpedStack(window, [], np.zeros((0, 1, rows, cols), np.float32),
                           np.full((0, rows, cols), np.nan, np.float32))

    # 无高程的格网无法做真正射，直接排除
    pts = np.stack([xs.ravel(), ys.ravel(), np.where(has_z, z, z_mid).ravel()], axis=1)
    drop = neighbor_drop(np.where(has_z, z, np.nan)).ravel()

    n_bands = images[views[0]].shape[0]
    pixels = np.full((len(views), n_bands, rows * cols), np.nan, np.float32)
    scores = np.full((len(views), rows * cols), np.nan, np.float32)

    for k, idx in enumerate(views):
        cam, pose = cameras[idx], poses[idx]
        u, v, ok = project(cam, pose, pts)
        inside = ok & cam.in_bounds(u, v, margin=cfg.edge_margin_px) & has_z.ravel()
        if not inside.any():
            continue
        seen = visibility_zbuffer(
            cam, pose, pts, u, v, inside,
            tolerance_m=cfg.occlusion_tolerance_m,
            drop_m=drop,
            gsd=grid.gsd,
            max_sub=cfg.facade_max_samples,
        )
        if not seen.any():
            continue
        img = images[idx]
        sel = np.nonzero(seen)[0]
        for b in range(n_bands):
            pixels[k, b, sel] = _sample_bilinear(img[b], u[sel], v[sel])
        s = view_score(cam, pose, pts[sel], u[sel], v[sel])
        # 采样越界（返回 nan）的点同样要判无效
        good = np.isfinite(pixels[k, :, sel]).all(axis=1) if n_bands > 1 else np.isfinite(
            pixels[k, 0, sel]
        )
        scores[k, sel] = np.where(good, s, np.nan)

    return WarpedStack(
        window,
        views,
        pixels.reshape(len(views), n_bands, rows, cols),
        scores.reshape(len(views), rows, cols),
    )


def best_view_mosaic(stack: WarpedStack) -> tuple[np.ndarray, np.ndarray]:
    """取质量分最高的视角直接铺图。用于快速出图与作为图割的初值。

    这不是最终成果：视角边界会留下明显接缝，需要 seamline + blend 处理。
    """
    n_views, n_bands, rows, cols = stack.pixels.shape
    out = np.full((n_bands, rows, cols), np.nan, np.float32)
    label = np.full((rows, cols), -1, np.int16)
    if n_views == 0:
        return out, label
    scores = np.where(np.isfinite(stack.scores), stack.scores, -np.inf)
    best = np.argmax(scores, axis=0)
    any_valid = np.isfinite(stack.scores).any(axis=0)
    rr, cc = np.indices((rows, cols))
    for b in range(n_bands):
        out[b] = np.where(any_valid, stack.pixels[best, b, rr, cc], np.nan)
    label = np.where(any_valid, best, -1).astype(np.int16)
    return out, label


def overlap_count(stack: WarpedStack) -> np.ndarray:
    """每个格网点有多少个视角可用。对应质量报告里的「重叠度」图。"""
    if stack.scores.shape[0] == 0:
        return np.zeros(stack.scores.shape[1:], np.uint8)
    return np.isfinite(stack.scores).sum(axis=0).astype(np.uint8)
