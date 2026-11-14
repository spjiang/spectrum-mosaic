"""合成测区：已知地形 + 已知地面纹理 + 已知相机，用来端到端验证几何正确性。

思路是反着走一遍成像过程：先在物方定义地形与贴在地形上的纹理，再对每张影像
的每个像素求视线与地形的交点、取该处纹理值，得到「真实」影像。这样密集匹配
该解出的高程、正射该还原的纹理都有解析真值可比。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ms_mosaic.camera import Camera, Pose, ray_directions

FLIGHT_Z = 1871.5
BASE_Z = 1760.0
TEX_ORIGIN = np.array([672900.0, 2619900.0])
TEX_GSD = 0.25
TEX_N = 1200


@dataclass
class Survey:
    cameras: dict[int, Camera]
    poses: dict[int, Pose]
    images: dict[int, np.ndarray]  # (bands, h, w) float32
    terrain: object  # callable(x, y) -> z
    texture: object  # callable(x, y) -> (bands,) 采样地面真值
    n_bands: int


def _smooth_noise(shape, seed, sigma=1.6):
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(seed)
    a = gaussian_filter(rng.random(shape).astype(np.float32), sigma)
    return (a - a.min()) / max(a.max() - a.min(), 1e-9) * 255.0


def make_texture(n_bands: int = 1, seed: int = 7):
    """生成贴在地面上的纹理，返回 (采样函数, 原始数组)。"""
    layers = np.stack([_smooth_noise((TEX_N, TEX_N), seed + i) for i in range(n_bands)])

    def sample(x, y):
        from ms_mosaic.dense import _sample_bilinear

        tc = (np.asarray(x) - TEX_ORIGIN[0]) / TEX_GSD
        tr = (np.asarray(y) - TEX_ORIGIN[1]) / TEX_GSD
        return np.stack([_sample_bilinear(layer, tc, tr) for layer in layers])

    return sample, layers


def make_terrain(relief: float = 18.0, wall: tuple | None = None):
    """地形函数。wall=(x0, x1, y0, y1, height) 会在该矩形内加一堵墙，用于遮挡测试。"""

    def terrain(x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        z = (
            BASE_Z
            + relief * np.sin((x - 673000.0) / 18.0)
            + 0.5 * relief * np.cos((y - 2620000.0) / 25.0)
        )
        if wall is not None:
            x0, x1, y0, y1, h = wall
            z = np.where((x >= x0) & (x <= x1) & (y >= y0) & (y <= y1), z + h, z)
        return z

    return terrain


def render_survey(
    centers: list[tuple[float, float]],
    *,
    relief: float = 18.0,
    wall: tuple | None = None,
    n_bands: int = 1,
    size: int = 512,
    focal: float = 560.0,
    seed: int = 7,
    ypr: tuple[float, float, float] = (0.0, -90.0, 0.0),
) -> Survey:
    """渲染一组影像。centers 为相对 (673030, 2620030) 的摄站平面偏移。"""
    terrain = make_terrain(relief, wall)
    texture, _ = make_texture(n_bands, seed)

    cam = Camera.initial("Color", size, size, kind="rgb").with_vector(
        np.array([focal, size / 2.0, size / 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    )
    uu, vv = np.meshgrid(np.arange(size), np.arange(size))
    u = uu.ravel().astype(float)
    v = vv.ravel().astype(float)

    poses, images = {}, {}
    for idx, (dx, dy) in enumerate(centers):
        center = np.array([673030.0 + dx, 2620030.0 + dy, FLIGHT_Z])
        pose = Pose.from_ypr(center, *ypr)
        poses[idx] = pose
        dirs = ray_directions(cam, pose, u, v)
        # 迭代求视线与地形的交点：地形平缓，十来次足够收敛
        z = np.full(dirs.shape[0], BASE_Z)
        for _ in range(16):
            t = (z - center[2]) / dirs[:, 2]
            z = terrain(center[0] + dirs[:, 0] * t, center[1] + dirs[:, 1] * t)
        t = (z - center[2]) / dirs[:, 2]
        x = center[0] + dirs[:, 0] * t
        y = center[1] + dirs[:, 1] * t
        images[idx] = texture(x, y).reshape(n_bands, size, size).astype(np.float32)

    return Survey(
        cameras={i: cam for i in poses},
        poses=poses,
        images=images,
        terrain=terrain,
        texture=texture,
        n_bands=n_bands,
    )


NADIR_GRID = [(0.0, 0.0), (14.0, 0.0), (0.0, 14.0), (14.0, 14.0), (7.0, -12.0)]


class MemoryCache:
    """把内存里的影像当缓存用，避免测试落盘。"""

    def __init__(self, arrays):
        self._arrays = arrays

    def __getitem__(self, index):
        return self._arrays[index]
