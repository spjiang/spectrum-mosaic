"""密集匹配：物方多视平面扫描 + SGM 代价聚合 → 2.5D 高程场。

为什么在物方而不是像方
----------------------
LiMapper 报告写明「密集点云 类型 2.5D」，成品也确实是高程场而非全 3D 网格。
所以这里直接在 DSM 格网上做平面扫描：对每个格网单元 (X, Y) 沿 Z 扫描候选高程，
把候选点投影到所有可见影像上比灰度一致性，取最优 Z。

相比「像方视差图 → 点云 → 融合 → 内插 DSM」的路线，物方扫描的好处是
天然多视、无需点云融合、直接得到规则格网，且并行粒度就是格网块。
这也是航空摄影测量里 object-space matching 的经典做法。

代价与聚合
----------
逐视图用窗口归一化互相关（NCC）与参考视图比对，取前 K 个最好的视图取均值，
对遮挡与局部失配有容忍度。随后沿 4 个方向做 SGM 式半全局动态规划
（Hirschmüller, PAMI 2008），抑制弱纹理区的噪声并保持地物边缘。

扫描范围由空三的稀疏点给出先验面。先验面经过核回归平滑后，相对树冠/
陡坎可偏十几米；±12 m 会把真表面挡在窗外，粗层一旦锁错，细层 ±1 m
也无法跳回。因此先在约 4 倍 GSD 的粗格网上用 ±24 m 定位，再回到成品
格网、在不少于 ±4 m 的窗口里精化（Gallup et al., CVPR 2007；架构 §4.5
「先粗 GSD 定 Z 范围，再逐层细化」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.grid import Grid
from ms_mosaic.rawio import read_gray8

NCC_WINDOW = 7
N_LAYERS = 48
Z_MARGIN_M = 24.0
MIN_REFINE_Z_MARGIN_M = 4.0
COARSE_GSD_FACTOR = 4
REFINE_LEVELS = 2
REFINE_Z_MARGIN_M = 8.0
MAX_VIEWS = 10
MIN_VIEWS = 2
MAX_TILT_DEG = 60.0  # 与报告「最大倾斜角 60 度」一致
SGM_P1 = 0.06
SGM_P2 = 0.35
MIN_NCC = 0.25
TOP_K_VIEWS = 4
TILE = 384
MAX_ANTIALIAS_SIGMA = 1.5
# 金字塔最深级：2048 px 的相片降 4 级还有 128 px，再深连航带重叠都对不上
MAX_PYRAMID_LEVEL = 4


@dataclass
class DenseConfig:
    window: int = NCC_WINDOW
    n_layers: int = N_LAYERS
    z_margin_m: float = Z_MARGIN_M
    max_views: int = MAX_VIEWS
    max_tilt_deg: float = MAX_TILT_DEG
    sgm_p1: float = SGM_P1
    sgm_p2: float = SGM_P2
    min_ncc: float = MIN_NCC
    top_k_views: int = TOP_K_VIEWS
    tile: int = TILE
    pyramid_levels: int = 2
    coarse_gsd_factor: int = COARSE_GSD_FACTOR
    # 粗格网定位之后，成品格网上的「剔粗差 → 重定先验 → 窄窗重扫」轮数与首轮半宽
    refine_levels: int = REFINE_LEVELS
    refine_z_margin_m: float = REFINE_Z_MARGIN_M
    # 粗层搜索半宽。None 表示与细层一致（实测加宽有害，见 _coarse_config）。
    coarse_z_margin_m: float | None = None
    workers: int | None = None


@dataclass
class HeightField:
    """2.5D 高程场。z 为 nan 表示该格网未解出高程。"""

    grid: Grid
    z: np.ndarray
    confidence: np.ndarray
    view_count: np.ndarray
    stats: dict = field(default_factory=dict)

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.z)


def refine_z_margin(
    prev_margin: float,
    n_layers: int,
    *,
    min_margin_m: float = MIN_REFINE_Z_MARGIN_M,
) -> float:
    """由粗到细时下一级的高程搜索半宽。

    旧写法取「上一级步长的 2 倍」，±12 m / 48 层时细层只剩 ±1 m。
    粗层若落在错误的 NCC 峰上（重复纹理、树冠/地面），细层无法跳回真表面。
    分层立体的常规是下一级覆盖上一级残差的数倍，且设下限
    （Scharstein & Szeliski, IJCV 2002；Gallup et al., CVPR 2007）。
    """
    step = 2.0 * float(prev_margin) / max(int(n_layers) - 1, 1)
    return max(float(min_margin_m), 3.0 * step)


def search_bound_hits(best: np.ndarray, n_layers: int) -> np.ndarray:
    """最优层贴在搜索窗两端：真高程很可能在窗外，不能当有效观测。"""
    return (best <= 0) | (best >= n_layers - 1)


def pyramid_level_for(grid_gsd: float, image_gsd: float, *, max_level: int = MAX_PYRAMID_LEVEL) -> int:
    """格网比影像粗 scale 倍时，该在第几级金字塔上采样。

    物方扫描是「按格网间距在影像上取采样点」。格网 0.55 m、影像 0.068 m 时
    相当于每隔 8 个像素取一个点，直接在原图上取就是 8 倍欠采样：高频纹理在
    不同视角折叠成不同假频，NCC 比的是假频不是地物，实测粗格网解出率只有
    12.8%。正解是先把影像降采样到与格网同一尺度（每级都是「先高斯再抽点」，
    见 ImageCache.at），再 1:1 取样 —— 这就是分层立体的标准做法
    （Scharstein & Szeliski, IJCV 2002；Gallup et al., CVPR 2007）。

    取 floor(log2(scale))：宁可留一点残余欠采样交给 antialias_sigma 补，
    也不要多降一级把真实纹理丢掉。
    """
    scale = float(grid_gsd) / max(float(image_gsd), 1e-9)
    if scale <= 1.0:
        return 0
    return int(np.clip(int(np.floor(np.log2(scale))), 0, int(max_level)))


def antialias_sigma(grid_gsd: float, image_gsd: float, *, level: int = 0) -> float:
    """在第 level 级金字塔上取样时，仍需补的高斯预平滑尺度（该级像素）。

    按采样定理，重采样到 scale 倍间距需要先把截止频率压到 1/scale，等效高斯
    尺度即 0.5·sqrt(scale²−1)。选对金字塔层后残余 scale < 2，sigma < 0.87，
    不会再碰到上限 —— 以前把 8 倍欠采样硬压在 1.5 px 上限里才是解不出的原因。
    """
    scale = float(grid_gsd) / max(float(image_gsd) * float(1 << int(level)), 1e-9)
    if scale <= 1.0:
        return 0.0
    return min(MAX_ANTIALIAS_SIGMA, 0.5 * float(np.sqrt(scale * scale - 1.0)))


class ImageCache:
    """按需读取灰度影像并缓存。全量 668 张 2048x1536 灰度约 2.1 GB。

    level 指定匹配用的金字塔层（由 pyramid_level_for 按格网/影像 GSD 定），
    sigma 是选定层之后仍需补的残余预平滑。两者都作用在 __getitem__ 返回的图上。
    """

    def __init__(
        self,
        paths: dict[int, Path],
        *,
        limit: int | None = None,
        sigma: float = 0.0,
        level: int = 0,
    ) -> None:
        self._paths = paths
        self._cache: dict[tuple[int, int], np.ndarray] = {}
        self._order: list[tuple[int, int]] = []
        self._limit = limit
        self._sigma = float(sigma)
        self._level = int(level)

    @property
    def level(self) -> int:
        return self._level

    def __getitem__(self, index: int) -> np.ndarray:
        """匹配用影像：降到 self.level 后再补残余平滑。"""
        return self.at(index, self._level, sigma=self._sigma)

    def _read(self, index: int) -> np.ndarray:
        """读第 0 级影像。抽出成钩子，便于替换影像来源而不重写金字塔逻辑。"""
        return read_gray8(self._paths[index]).astype(np.float32)

    def at(self, index: int, level: int = 0, *, sigma: float = 0.0) -> np.ndarray:
        """第 level 级金字塔影像（每级边长减半），可再叠一次 sigma 平滑。

        金字塔层不能靠直接抽点得到：抽点前必须把截止频率压到新采样率的一半，
        否则高频折叠成假频，粗层 NCC 就在比较两幅不同的假频图案。
        残余平滑在该级上做，不能在 level 0 上做 —— 抽点会把它一起缩小 2^level 倍。
        """
        key = (index, level if sigma <= 0.05 else -level - 1)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if sigma > 0.05:
            from scipy.ndimage import gaussian_filter

            arr = gaussian_filter(self.at(index, level), sigma, mode="nearest")
        elif level == 0:
            arr = self._read(index)
        else:
            from scipy.ndimage import gaussian_filter

            arr = gaussian_filter(self.at(index, level - 1), 1.0, mode="nearest")[::2, ::2]
        self._cache[key] = np.ascontiguousarray(arr)
        self._order.append(key)
        if self._limit is not None and len(self._order) > self._limit:
            self._cache.pop(self._order.pop(0), None)
        return self._cache[key]

    def pixel_coords(self, u: np.ndarray, v: np.ndarray, level: int | None = None):
        """全分辨率像素坐标 → 第 level 级坐标（默认本缓存的匹配层）。

        用 (u+0.5)/2^l − 0.5 而不是 u/2^l：前者保持像元中心对齐，后者会引入
        半个粗层像素的系统性偏移，在 NCC 里表现为一个固定的错配。
        """
        lv = self._level if level is None else int(level)
        if lv == 0:
            return u, v
        s = 1.0 / float(1 << lv)
        return (u + 0.5) * s - 0.5, (v + 0.5) * s - 0.5


def off_nadir_deg(pose: Pose) -> float:
    d = pose.viewing_direction
    return float(np.degrees(np.arccos(min(1.0, max(-1.0, -d[2])))))


def prior_surface(points: np.ndarray, grid: Grid, *, smooth_cells: float = 6.0) -> np.ndarray:
    """由稀疏点插值出扫描先验面。

    先把点落到格网取中位数（抗粗差），再用「高斯加权 + 归一化」把空洞填上 ——
    等价于对稀疏观测做核回归，比 griddata 的线性插值更稳，也不会在凸包外爆掉。
    """
    from scipy.ndimage import gaussian_filter

    inv = ~grid.transform
    cols, rows = inv * (points[:, 0], points[:, 1])
    cols = np.floor(np.asarray(cols)).astype(int)
    rows = np.floor(np.asarray(rows)).astype(int)
    inside = (cols >= 0) & (cols < grid.width) & (rows >= 0) & (rows < grid.height)
    if not inside.any():
        raise ValueError("稀疏点全部落在格网之外，检查格网范围与坐标系")
    cols, rows, zs = cols[inside], rows[inside], points[inside, 2]

    flat = rows * grid.width + cols
    order = np.argsort(flat)
    flat_sorted, zs_sorted = flat[order], zs[order]
    uniq, start = np.unique(flat_sorted, return_index=True)
    medians = np.array(
        [np.median(zs_sorted[a:b]) for a, b in zip(start, list(start[1:]) + [len(zs_sorted)])]
    )

    acc = np.zeros(grid.height * grid.width, np.float64)
    wgt = np.zeros_like(acc)
    acc[uniq] = medians
    wgt[uniq] = 1.0
    acc = acc.reshape(grid.shape)
    wgt = wgt.reshape(grid.shape)

    num = gaussian_filter(acc, smooth_cells, mode="nearest")
    den = gaussian_filter(wgt, smooth_cells, mode="nearest")
    out = np.where(den > 1e-6, num / np.maximum(den, 1e-12), np.nan)
    if np.isnan(out).any():
        # 极少数远离所有点的角落，用更大的核再补一次
        num2 = gaussian_filter(acc, smooth_cells * 6.0, mode="nearest")
        den2 = gaussian_filter(wgt, smooth_cells * 6.0, mode="nearest")
        fill = np.where(den2 > 1e-9, num2 / np.maximum(den2, 1e-12), float(np.median(medians)))
        out = np.where(np.isnan(out), fill, out)
    return out.astype(np.float32)


def _sample_bilinear(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """双线性采样，越界返回 nan。"""
    h, w = image.shape
    finite = np.isfinite(u) & np.isfinite(v)
    us = np.where(finite, u, 0.0)
    vs = np.where(finite, v, 0.0)
    # 有效范围按闭区间取。若写成 u0 < w-1，恰好落在最后一行/列的采样会被误判越界，
    # 正射与 DSM 就会沿像幅边缘出现一圈空洞。
    ok = finite & (us >= 0.0) & (us <= w - 1) & (vs >= 0.0) & (vs <= h - 1)
    u0 = np.floor(us).astype(np.int32)
    v0 = np.floor(vs).astype(np.int32)
    u0c = np.clip(u0, 0, w - 2)
    v0c = np.clip(v0, 0, h - 2)
    u, v = us, vs
    du = (u - u0c).astype(np.float32)
    dv = (v - v0c).astype(np.float32)
    i00 = image[v0c, u0c]
    i10 = image[v0c, u0c + 1]
    i01 = image[v0c + 1, u0c]
    i11 = image[v0c + 1, u0c + 1]
    top = i00 * (1 - du) + i10 * du
    bot = i01 * (1 - du) + i11 * du
    out = top * (1 - dv) + bot * dv
    return np.where(ok, out, np.nan)


def _box(a: np.ndarray, window: int) -> np.ndarray:
    """窗口求和（nan 视作 0）。用积分图，和窗口大小无关。"""
    from scipy.ndimage import uniform_filter

    return uniform_filter(np.nan_to_num(a, nan=0.0), window, mode="constant", cval=0.0) * (
        window * window
    )


def _windowed_ncc(ref: np.ndarray, other: np.ndarray, window: int) -> np.ndarray:
    """逐像元窗口 NCC。两张图任一为 nan 的位置不计入。"""
    mask = np.isfinite(ref) & np.isfinite(other)
    a = np.where(mask, ref, 0.0)
    b = np.where(mask, other, 0.0)
    n = _box(mask.astype(np.float32), window)
    sa = _box(a, window)
    sb = _box(b, window)
    saa = _box(a * a, window)
    sbb = _box(b * b, window)
    sab = _box(a * b, window)
    valid = n >= 0.6 * window * window
    n_safe = np.maximum(n, 1.0)
    cov = sab / n_safe - (sa / n_safe) * (sb / n_safe)
    va = np.maximum(saa / n_safe - (sa / n_safe) ** 2, 0.0)
    vb = np.maximum(sbb / n_safe - (sb / n_safe) ** 2, 0.0)
    denom = np.sqrt(va * vb)
    # 方差过小说明是均匀区，NCC 无意义
    ncc = np.where(denom > 1e-3, cov / np.maximum(denom, 1e-12), np.nan)
    return np.where(valid, ncc, np.nan)


def _sgm_aggregate(cost: np.ndarray, p1: float, p2: float) -> np.ndarray:
    """沿 4 个方向做半全局动态规划聚合。cost 形如 (n_layers, h, w)。

    惩罚项沿用 Hirschmüller 的两级形式：相邻一层罚 p1，跳跃更多层罚 p2。
    """
    n_z = cost.shape[0]
    acc = np.zeros_like(cost)
    # (轴, 步进)：沿行的正反两遍 + 沿列的正反两遍。
    # 注意这里是 (axis, step) 而不是 (dy, dx)：写成 (1, 0) 会让 step=0，
    # 动态规划就变成原地读自己，该方向失效并把惩罚项稀释掉。
    shifts = ((0, 1), (0, -1), (1, 1), (1, -1))
    layer_idx = np.arange(n_z)
    jump = np.abs(layer_idx[:, None] - layer_idx[None, :])
    penalty = np.where(jump == 0, 0.0, np.where(jump == 1, p1, p2))

    for axis, step in shifts:
        agg = cost.copy()
        length = cost.shape[1 + axis]
        order = range(1, length) if step > 0 else range(length - 2, -1, -1)
        for k in order:
            prev = k - step
            if axis == 0:
                prior = agg[:, prev, :]
            else:
                prior = agg[:, :, prev]
            # min_j (prior_j + penalty_ij)，减去 prior 最小值防止数值累积
            best = np.min(prior[None, :, :] + penalty[:, :, None], axis=1)
            best = best - prior.min(axis=0, keepdims=True)
            if axis == 0:
                agg[:, k, :] = cost[:, k, :] + best
            else:
                agg[:, :, k] = cost[:, :, k] + best
        acc += agg
    return acc / len(shifts)


def _subpixel_z(cost: np.ndarray, best: np.ndarray, z_values: np.ndarray) -> np.ndarray:
    """对代价曲线做抛物线拟合，把高程精度做到层间。"""
    n_z = cost.shape[0]
    k = np.clip(best, 1, n_z - 2)
    rows, cols = np.indices(best.shape)
    c0 = cost[k - 1, rows, cols]
    c1 = cost[k, rows, cols]
    c2 = cost[k + 1, rows, cols]
    denom = c0 - 2.0 * c1 + c2
    flat = np.abs(denom) <= 1e-9  # 代价曲线在该点是平的，拟合不出顶点
    delta = np.where(flat, 0.0, 0.5 * (c0 - c2) / np.where(flat, 1.0, denom))
    delta = np.clip(delta, -1.0, 1.0)
    step = z_values[1] - z_values[0]
    return z_values[k] + delta * step


def _visible_views(
    grid: Grid,
    window: tuple[int, int, int, int],
    z_mid: float,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    cfg: DenseConfig,
) -> list[int]:
    """挑出能看到该格网块的影像，按离星下点的距离排序（越正越优先）。"""
    row0, col0, rows, cols = window
    xs, ys = grid.cell_centers(window)
    corners = np.array(
        [
            [xs[0, 0], ys[0, 0], z_mid],
            [xs[0, -1], ys[0, -1], z_mid],
            [xs[-1, 0], ys[-1, 0], z_mid],
            [xs[-1, -1], ys[-1, -1], z_mid],
            [xs[rows // 2, cols // 2], ys[rows // 2, cols // 2], z_mid],
        ]
    )
    center = corners[-1]
    scored = []
    for idx, pose in poses.items():
        if off_nadir_deg(pose) > cfg.max_tilt_deg:
            continue
        cam = cameras[idx]
        u, v, valid = np.array([]), np.array([]), np.array([])
        from ms_mosaic.camera import project

        u, v, valid = project(cam, pose, corners)
        inside = valid & cam.in_bounds(u, v, margin=cfg.window)
        if not inside.any():
            continue
        # 星下点到块中心的水平距离，近的透视变形小
        dist = float(np.linalg.norm(pose.center[:2] - center[:2]))
        scored.append((-int(inside.sum()), dist, idx))
    scored.sort()
    return [idx for _, _, idx in scored[: cfg.max_views]]


def _sweep_once(
    grid: Grid,
    window: tuple[int, int, int, int],
    prior_z: np.ndarray,
    views: list[int],
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    images: ImageCache,
    cfg: DenseConfig,
    z_margin: float,
    n_layers: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """在先验面附近扫一遍给定的高程区间。返回 (z, 置信度, 参与视图数)。"""
    from ms_mosaic.camera import project

    row0, col0, rows, cols = window
    xs, ys = grid.cell_centers(window)
    nan = np.full((rows, cols), np.nan, np.float32)

    z_values = np.linspace(-z_margin, z_margin, n_layers, dtype=np.float32)
    ref_idx = views[0]
    others = views[1:]

    # 先把参考视图在每一层的采样值算出来，其余视图逐个与之比 NCC。
    # 逐层记录有效视图数：质量门限要卡所选层的原始代价，不能卡全层的最优值。
    cost = np.full((n_layers, rows, cols), np.nan, np.float32)
    n_views = np.zeros((n_layers, rows, cols), np.uint8)

    flat_x = xs.ravel()
    flat_y = ys.ravel()
    for li, dz in enumerate(z_values):
        zz = (prior_z + dz).ravel()
        pts = np.stack([flat_x, flat_y, zz], axis=1)

        # 采样点坐标要换到匹配所用的金字塔层，否则会在原图上按格网间距抽点欠采样
        u, v, valid = project(cameras[ref_idx], poses[ref_idx], pts)
        ul, vl = images.pixel_coords(u, v)
        ref = np.where(valid, _sample_bilinear(images[ref_idx], ul, vl), np.nan).reshape(rows, cols)

        nccs = []
        for other in others:
            u2, v2, valid2 = project(cameras[other], poses[other], pts)
            u2l, v2l = images.pixel_coords(u2, v2)
            arr = np.where(valid2, _sample_bilinear(images[other], u2l, v2l), np.nan).reshape(
                rows, cols
            )
            nccs.append(_windowed_ncc(ref, arr, cfg.window))
        if not nccs:
            continue
        stack = np.stack(nccs, axis=0)
        n_ok = np.sum(np.isfinite(stack), axis=0)
        # 取最好的 K 个视图取均值：对遮挡与个别失配有容忍度。
        # 无效视图必须排除在均值之外：若拿哨兵值凑满 K 个，代价就会被
        # 「有多少视图恰好落在幅内」左右 —— 而那随假设高程变化，是几何
        # 假象不是匹配证据，会把整个高程面系统性地拉偏。
        k = min(cfg.top_k_views, stack.shape[0])
        top = np.sort(np.where(np.isfinite(stack), stack, -np.inf), axis=0)[-k:]
        top_ok = np.isfinite(top)
        n_top = top_ok.sum(axis=0)
        score = np.where(
            n_top > 0,
            np.sum(np.where(top_ok, top, 0.0), axis=0) / np.maximum(n_top, 1),
            np.nan,
        )
        cost[li] = 1.0 - score
        n_views[li] = np.minimum(n_ok, 255).astype(np.uint8)

    finite = np.isfinite(cost)
    if not finite.any():
        return nan, nan.copy(), np.zeros((rows, cols), np.uint8)
    # SGM 需要完整代价体，无效处填一个大值
    filled_cost = np.where(finite, cost, 2.0).astype(np.float32)
    agg = _sgm_aggregate(filled_cost, cfg.sgm_p1, cfg.sgm_p2)

    best = np.argmin(agg, axis=0)
    rows_i, cols_i = np.indices(best.shape)
    best_cost = agg[best, rows_i, cols_i]
    # 所选层的原始代价与视图数才是质量指标；agg 里掺了 SGM 的平滑惩罚，
    # 拿它卡门限会把「邻域一致但自身纹理弱」和「匹配确实不好」混为一谈。
    raw_cost = cost[best, rows_i, cols_i]
    count = n_views[best, rows_i, cols_i]
    z = prior_z + _subpixel_z(agg, best, z_values)

    # 置信度：最优与次优代价的相对差。次优只在距最优 2 层以外找，
    # 否则相邻层的代价天然接近，会把好点判成低置信。
    masked = agg.copy()
    for d in (-2, -1, 0, 1, 2):
        k = np.clip(best + d, 0, n_layers - 1)
        masked[k, rows_i, cols_i] = np.inf
    second = np.min(masked, axis=0)
    confidence = np.where(np.isfinite(second), (second - best_cost) / np.maximum(second, 1e-6), 0.0)

    # 宽搜索窗贴边 = 真表面在窗外。细层（几米）贴边更常见，留给下一级
    # 或补洞，不能在这里整片作废。
    saturated = search_bound_hits(best, n_layers) if z_margin >= 8.0 else np.zeros_like(best, dtype=bool)
    bad = (
        (count < MIN_VIEWS)
        | (~np.isfinite(z))
        | (~np.isfinite(raw_cost))
        | (raw_cost > 1.0 - cfg.min_ncc)
        | saturated
    )
    z = np.where(bad, np.nan, z).astype(np.float32)
    confidence = np.where(bad, np.nan, confidence).astype(np.float32)
    return z, confidence, np.where(bad, 0, count).astype(np.uint8)


def _next_prior(z: np.ndarray, fallback: np.ndarray, smooth_cells: float = 2.0) -> np.ndarray:
    """把上一层的结果整理成下一层的先验面：剔粗差 + 补洞 + 轻度平滑。

    未解出的格网退回上一层的先验，避免下一层在错误的位置窄范围搜索。

    必须先剔粗差再做先验。粗层锁错的格网若直接进先验，下一层就在错误高程
    附近开窄窗搜索，错误被固化下来；剔掉之后由邻域的正确高程补出先验，
    细层才有机会跳回真表面（分层立体的常规做法）。
    """
    from scipy.ndimage import gaussian_filter

    from ms_mosaic.dsm import remove_spikes

    z = remove_spikes(np.asarray(z, np.float64))
    ok = np.isfinite(z).astype(np.float32)
    if ok.sum() < 4:
        return fallback
    num = gaussian_filter(np.nan_to_num(z, nan=0.0) * ok, smooth_cells, mode="nearest")
    den = gaussian_filter(ok, smooth_cells, mode="nearest")
    grown = np.where(den > 1e-4, num / np.maximum(den, 1e-12), np.nan)
    return np.where(np.isfinite(grown), grown, fallback).astype(np.float32)


def sweep_tile(
    grid: Grid,
    window: tuple[int, int, int, int],
    prior_z: np.ndarray,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    images: ImageCache,
    cfg: DenseConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """对一个格网块做由粗到细的多层平面扫描。返回 (z, 置信度, 参与视图数)。

    单层扫描要同时覆盖大搜索范围和细高程分辨率，层数会爆炸：±24 m 做到
    0.1 m 分辨率需要 480 层。改成两级后，第一级 ±24 m 定位，第二级在
    不少于 ±4 m 的窗口内精化，避免粗层锁错面后无法跳回。
    """
    row0, col0, rows, cols = window
    z_mid = float(np.nanmedian(prior_z))
    views = _visible_views(grid, window, z_mid, cameras, poses, cfg)
    nan = np.full((rows, cols), np.nan, np.float32)
    if len(views) < MIN_VIEWS:
        return nan, nan.copy(), np.zeros((rows, cols), np.uint8)

    prior = np.asarray(prior_z, np.float32)
    margin, layers = cfg.z_margin_m, cfg.n_layers
    z = conf = nan
    count = np.zeros((rows, cols), np.uint8)
    for level in range(max(1, cfg.pyramid_levels)):
        z, conf, count = _sweep_once(
            grid, window, prior, views, cameras, poses, images, cfg, margin, layers
        )
        if level + 1 >= max(1, cfg.pyramid_levels):
            break
        # 下一级必须能跳回粗层可能锁错的面，不能只留一个步长
        margin = refine_z_margin(margin, layers)
        prior = _next_prior(z, prior)
    return z, conf, count


def _image_gsd(cameras: dict[int, Camera], poses: dict[int, Pose], prior: np.ndarray) -> float:
    """影像地面采样距 ≈ 航高 / 焦距。"""
    ground = float(np.nanmedian(prior))
    agl = np.array([poses[i].center[2] - ground for i in poses])
    return float(np.median([np.median(agl) / cameras[i].f for i in cameras]))


def _match_on_grid(
    grid: Grid,
    prior: np.ndarray,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    image_paths: dict[int, Path],
    cfg: DenseConfig,
    *,
    log=None,
    workers: int | None = None,
    label: str = "密集匹配",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """在给定格网与先验面上扫一遍，返回 (z, conf, views, image_gsd)。"""
    from functools import partial

    from ms_mosaic.parallel import map_tiles

    img_gsd = _image_gsd(cameras, poses, prior)
    level = pyramid_level_for(grid.gsd, img_gsd)
    sigma = antialias_sigma(grid.gsd, img_gsd, level=level)
    if log is not None:
        log(
            f"{label}：格网 {grid.width}x{grid.height} @ {grid.gsd:.4f} m，"
            f"±{cfg.z_margin_m:.1f} m × {cfg.n_layers} 层，"
            f"影像 {img_gsd:.4f} m/px → 金字塔第 {level} 级（{img_gsd * (1 << level):.4f} m/px）"
            f"，残余抗锯齿 {sigma:.2f} px"
        )

    z = np.full(grid.shape, np.nan, np.float32)
    conf = np.full(grid.shape, np.nan, np.float32)
    views = np.zeros(grid.shape, np.uint8)
    overlap = cfg.window
    windows = list(grid.tiles(cfg.tile, overlap=overlap))
    payload = {
        "grid": grid,
        "prior": prior,
        "cameras": cameras,
        "poses": poses,
        "paths": image_paths,
        "cfg": cfg,
        "sigma": sigma,
        "level": level,
    }
    n_workers = workers if workers is not None else cfg.workers
    done = 0
    for _, window, (tile_z, tile_conf, tile_views) in map_tiles(
        windows,
        partial(_dense_setup, payload),
        _dense_fn,
        workers=n_workers,
        log=log,
        label=label,
    ):
        paste_tile(z, tile_z, window, overlap, grid)
        paste_tile(conf, tile_conf, window, overlap, grid)
        paste_tile(views, tile_views, window, overlap, grid)
        done += 1
        if log is not None and n_workers == 1 and (done % 10 == 0 or done == len(windows)):
            log(f"{label} {done}/{len(windows)} 块，已解出 {np.isfinite(z).mean():.1%}")
    return z, conf, views, img_gsd


def _coarse_config(cfg: DenseConfig, points: np.ndarray) -> DenseConfig:
    """粗层配置。

    曾试过把粗层搜索带按地形起伏加宽到 ±50 m（Collins 空间扫描的做法），想让
    先验偏低的山脊也能够到真表面。实测反而变差：山顶窗口粗差率 15.4% → 21.9%，
    成品解出率 56.0% → 39.3% —— 宽带里多出来的层给了假匹配更多机会，得不偿失。
    山顶偏低的真正原因不在搜索带，而在解不出的格网被从低处填了上来（见 dsm.py
    的 fill 相关说明）。这里保持与细层同一配置，改动记录留在这里避免再走回头路。
    """
    from dataclasses import replace

    if cfg.coarse_z_margin_m is None:
        return replace(cfg)
    return replace(cfg, z_margin_m=float(cfg.coarse_z_margin_m))


def compute_height_field(
    grid: Grid,
    points: np.ndarray,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    image_paths: dict[int, Path],
    *,
    cfg: DenseConfig | None = None,
    log=None,
    workers: int | None = None,
) -> HeightField:
    """对整个格网做密集匹配。分块处理，块间留窗口重叠，默认可多进程。

    成品格网细于 0.2 m 时先在 4 倍 GSD 上用宽窗口定位，再回到成品格网精化，
    避免 ±1 m 细层把粗层的错误表面锁死。
    """
    from dataclasses import replace

    cfg = cfg or DenseConfig()
    prior = prior_surface(points, grid)
    n_workers = workers if workers is not None else cfg.workers
    factor = int(cfg.coarse_gsd_factor)
    coarse_used = False
    coarse_margin = 0.0
    coarse_layers = 0
    if factor >= 2 and min(grid.width, grid.height) >= 64:
        coarse_grid = grid.refine(float(factor))
        if coarse_grid.gsd > grid.gsd * 1.5 and min(coarse_grid.width, coarse_grid.height) >= 16:
            coarse_used = True
            coarse_prior = prior_surface(points, coarse_grid)
            coarse_cfg = _coarse_config(cfg, points)
            coarse_margin = float(coarse_cfg.z_margin_m)
            coarse_layers = int(coarse_cfg.n_layers)
            if log is not None:
                log(
                    f"粗格网搜索 ±{coarse_cfg.z_margin_m:.1f} m × {coarse_cfg.n_layers} 层"
                    f"（层间距 {2 * coarse_cfg.z_margin_m / max(coarse_cfg.n_layers - 1, 1):.2f} m）"
                )
            z_c, _, _, _ = _match_on_grid(
                coarse_grid,
                coarse_prior,
                cameras,
                poses,
                image_paths,
                coarse_cfg,
                log=log,
                workers=n_workers,
                label="粗格网密集匹配",
            )
            from ms_mosaic.dsm import resample_height

            up = resample_height(z_c, coarse_grid, grid)
            prior = np.where(np.isfinite(up), up, prior).astype(np.float32)
            cfg = replace(
                cfg,
                z_margin_m=max(MIN_REFINE_Z_MARGIN_M * 2.0, float(cfg.refine_z_margin_m)),
                pyramid_levels=max(1, int(cfg.refine_levels)),
            )
            if log is not None:
                log(
                    f"粗格网解出 {np.isfinite(z_c).mean():.1%}，"
                    f"细格网搜索 ±{cfg.z_margin_m:.1f} m × {cfg.pyramid_levels} 轮"
                )

    z, conf, views, img_gsd = _match_on_grid(
        grid,
        prior,
        cameras,
        poses,
        image_paths,
        cfg,
        log=log,
        workers=n_workers,
        label="密集匹配",
    )

    valid = np.isfinite(z)
    stats = {
        "n_cells": int(z.size),
        "n_valid": int(valid.sum()),
        "fill_ratio": float(valid.mean()),
        "z_min": float(np.nanmin(z)) if valid.any() else float("nan"),
        "z_median": float(np.nanmedian(z)) if valid.any() else float("nan"),
        "z_max": float(np.nanmax(z)) if valid.any() else float("nan"),
        "mean_views": float(views[valid].mean()) if valid.any() else 0.0,
        "gsd": grid.gsd,
        "image_gsd": img_gsd,
        "pyramid_level": int(pyramid_level_for(grid.gsd, img_gsd)),
        "antialias_sigma_px": antialias_sigma(
            grid.gsd, img_gsd, level=pyramid_level_for(grid.gsd, img_gsd)
        ),
        "coarse_gsd": bool(coarse_used),
        "z_margin_m": float(cfg.z_margin_m),
        "coarse_z_margin_m": coarse_margin,
        "coarse_n_layers": coarse_layers,
    }
    return HeightField(grid, z, conf, views, stats)


def paste_tile(
    dst: np.ndarray, tile: np.ndarray, window: tuple[int, int, int, int], overlap: int, grid: Grid
) -> None:
    """把带重叠边的块写进整幅，丢掉窗口算子的边界晕圈。

    dst / tile 可以是 (h, w) 或 (bands, h, w)。
    """
    row0, col0, rows, cols = window
    ir0 = overlap if row0 > 0 else 0
    ic0 = overlap if col0 > 0 else 0
    ir1 = rows - overlap if row0 + rows < grid.height else rows
    ic1 = cols - overlap if col0 + cols < grid.width else cols
    sl = (slice(row0 + ir0, row0 + ir1), slice(col0 + ic0, col0 + ic1))
    src = (slice(ir0, ir1), slice(ic0, ic1))
    if dst.ndim == 2:
        dst[sl] = tile[src]
    else:
        dst[(slice(None),) + sl] = tile[(slice(None),) + src]


def _dense_setup(payload: dict):
    from ms_mosaic.parallel import CACHE_PER_WORKER

    return {
        "grid": payload["grid"],
        "prior": payload["prior"],
        "cameras": payload["cameras"],
        "poses": payload["poses"],
        "cfg": payload["cfg"],
        "images": ImageCache(
            payload["paths"],
            sigma=payload["sigma"],
            level=payload.get("level", 0),
            limit=CACHE_PER_WORKER,
        ),
    }


def _dense_fn(ctx: dict, window: tuple[int, int, int, int]):
    row0, col0, rows, cols = window
    return sweep_tile(
        ctx["grid"],
        window,
        ctx["prior"][row0 : row0 + rows, col0 : col0 + cols],
        ctx["cameras"],
        ctx["poses"],
        ctx["images"],
        ctx["cfg"],
    )
