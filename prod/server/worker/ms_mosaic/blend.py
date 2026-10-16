"""辐射融合：全局曝光补偿 + 拉普拉斯金字塔多频段融合。

两个环节解决两类不同的问题，缺一不可：

曝光补偿（低频、全局）
    各次曝光的增益/光照不同，直接拼会出现整片色块。按 Brown & Lowe (IJCV 2007)
    的做法，对每张影像解一个增益因子，使所有重叠区的灰度差平方和最小，并加一个
    把增益拉向 1 的正则项，防止整体解漂移（增益全体乘同一个常数不改变重叠差）。

多频段融合（高频、局部）
    补偿后仍有残差与配准误差。Burt & Adelson (1983) 的做法是按频段用不同宽度的
    过渡带：低频用宽过渡抹掉亮度台阶，高频用窄过渡保住纹理不糊。直接做单一宽度的
    羽化则必然二选一 —— 窄了留亮度台阶，宽了纹理发虚。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ms_mosaic.ortho import WarpedStack

GAIN_SIGMA_N = 10.0
GAIN_SIGMA_G = 0.3
GAIN_LIMITS = (0.65, 1.55)
BLEND_LEVELS = 7
MIN_OVERLAP_CELLS = 50


@dataclass
class OverlapStats:
    """跨块累计的重叠统计，用于全局解增益。键为 (影像i, 影像j)，i<j。"""

    counts: dict[tuple[int, int], int] = field(default_factory=dict)
    sums: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)

    def add(self, i: int, j: int, sum_i: float, sum_j: float, n: int) -> None:
        if n <= 0:
            return
        key = (i, j) if i < j else (j, i)
        if i > j:
            sum_i, sum_j = sum_j, sum_i
        c = self.counts.get(key, 0)
        a, b = self.sums.get(key, (0.0, 0.0))
        self.counts[key] = c + n
        self.sums[key] = (a + sum_i, b + sum_j)

    def means(self, key: tuple[int, int]) -> tuple[float, float]:
        n = self.counts[key]
        a, b = self.sums[key]
        return a / n, b / n

    @property
    def images(self) -> list[int]:
        seen: set[int] = set()
        for i, j in self.counts:
            seen.add(i)
            seen.add(j)
        return sorted(seen)


def merge_overlap(dst: OverlapStats, src: OverlapStats) -> OverlapStats:
    """把另一块的重叠统计并进来。多进程按块统计后在主进程汇总。"""
    for key, n in src.counts.items():
        a, b = src.sums[key]
        dst.add(key[0], key[1], a, b, n)
    return dst


def accumulate_overlap(
    stack: WarpedStack, acc: OverlapStats, *, channel: int | None = None
) -> OverlapStats:
    """把一个格网块里各视角两两重叠区的灰度和累加进全局统计。

    channel 为 None 时 RGB 用三通道均值（亮度），否则只用该通道 ——
    分通道解增益才能把偏黄/偏青的整帧色差拉平。
    """
    n_views = stack.pixels.shape[0]
    if n_views < 2:
        return acc
    if channel is None:
        inten = stack.pixels[:, 0]
        if stack.n_bands > 1:
            with np.errstate(all="ignore"):
                inten = np.nanmean(stack.pixels, axis=1)
    else:
        inten = stack.pixels[:, int(channel)]
    valid = np.isfinite(stack.scores) & np.isfinite(inten)
    for a in range(n_views):
        for b in range(a + 1, n_views):
            both = valid[a] & valid[b]
            n = int(both.sum())
            if n < MIN_OVERLAP_CELLS:
                continue
            acc.add(
                stack.views[a], stack.views[b],
                float(inten[a][both].sum()), float(inten[b][both].sum()), n,
            )
    return acc


def solve_gains(
    acc: OverlapStats,
    *,
    sigma_n: float = GAIN_SIGMA_N,
    sigma_g: float = GAIN_SIGMA_G,
    limits: tuple[float, float] = GAIN_LIMITS,
) -> dict[int, float]:
    """解各影像的曝光增益。

    最小化 Σ_ij N_ij (g_i·Ī_ij − g_j·Ī_ji)² / σ_N² + Σ_i (1−g_i)² / σ_g²。
    后一项不可省：只有重叠项时，把所有增益同乘一个常数解不变，系统是奇异的。
    """
    images = acc.images
    if not images:
        return {}
    index = {img: k for k, img in enumerate(images)}
    n = len(images)
    a = np.zeros((n, n))
    b = np.zeros(n)

    wg = 1.0 / (sigma_g**2)
    for k in range(n):
        a[k, k] += wg
        b[k] += wg

    for key, count in acc.counts.items():
        i, j = key
        mi, mj = acc.means(key)
        w = count / (sigma_n**2)
        ki, kj = index[i], index[j]
        a[ki, ki] += w * mi * mi
        a[kj, kj] += w * mj * mj
        a[ki, kj] -= w * mi * mj
        a[kj, ki] -= w * mi * mj

    gains = np.linalg.solve(a, b)
    # 重叠项对整体乘数无约束；(1−g)² 在影像很多时会被数据项淹没，
    # 全量 654 张曾全部落到下限 0.5，后面再按商业中位数一拉，相对增益为 0。
    # 先把中位置回 1，再裁剪，相对曝光差才进得了正射。
    med = float(np.median(gains))
    if abs(med) > 1e-6:
        gains = gains / med
    lo, hi = limits
    return {img: float(np.clip(gains[index[img]], lo, hi)) for img in images}


def apply_gains(
    stack: WarpedStack,
    gains: dict[int, float] | list[dict[int, float]],
) -> WarpedStack:
    """把增益乘到各视角像素上。未估出增益的视角按 1.0 处理。

    传入 list 时按通道分别乘（RGB 色差）；单个 dict 则三通道共用亮度增益。
    """
    if not gains:
        return stack
    px = stack.pixels.copy()
    n_bands = px.shape[1]
    if isinstance(gains, list):
        for b in range(min(n_bands, len(gains))):
            g = np.array([gains[b].get(v, 1.0) for v in stack.views], np.float32)
            px[:, b] *= g[:, None, None]
    else:
        g = np.array([gains.get(v, 1.0) for v in stack.views], np.float32)
        px *= g[:, None, None, None]
    return WarpedStack(stack.window, stack.views, px, stack.scores)


def _pyr_down(a: np.ndarray) -> np.ndarray:
    from scipy.ndimage import gaussian_filter

    return gaussian_filter(a, 1.0, mode="nearest")[..., ::2, ::2]


def _pyr_up(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    from scipy.ndimage import zoom

    factors = (shape[0] / a.shape[-2], shape[1] / a.shape[-1])
    out = zoom(a, (1,) * (a.ndim - 2) + factors, order=1, mode="nearest")
    # zoom 的尺寸可能差 1，裁/补到目标大小
    out = out[..., : shape[0], : shape[1]]
    if out.shape[-2:] != shape:
        pad = [(0, 0)] * (out.ndim - 2) + [
            (0, shape[0] - out.shape[-2]), (0, shape[1] - out.shape[-1])
        ]
        out = np.pad(out, pad, mode="edge")
    return out


def laplacian_pyramid(img: np.ndarray, levels: int) -> list[np.ndarray]:
    """构建拉普拉斯金字塔。img 形如 (..., h, w)。"""
    gauss = [img]
    for _ in range(levels - 1):
        prev = gauss[-1]
        if min(prev.shape[-2:]) < 4:
            break
        gauss.append(_pyr_down(prev))
    lap = []
    for k in range(len(gauss) - 1):
        lap.append(gauss[k] - _pyr_up(gauss[k + 1], gauss[k].shape[-2:]))
    lap.append(gauss[-1])
    return lap


def collapse_pyramid(lap: list[np.ndarray]) -> np.ndarray:
    out = lap[-1]
    for k in range(len(lap) - 2, -1, -1):
        out = lap[k] + _pyr_up(out, lap[k].shape[-2:])
    return out


def multiband_blend(
    stack: WarpedStack, labels: np.ndarray, *, levels: int = BLEND_LEVELS
) -> np.ndarray:
    """按标号做多频段融合，返回 (bands, h, w)。

    每个视角的权重由「该视角被选中的区域」经高斯金字塔逐级平滑得到：
    层级越高（频率越低）过渡带越宽，正好实现「低频宽过渡、高频窄过渡」。
    """
    n_views, n_bands, h, w = stack.pixels.shape
    if n_views == 0:
        return np.full((n_bands, h, w), np.nan, np.float32)

    valid = np.isfinite(stack.scores) & np.isfinite(stack.pixels).all(axis=1)
    covered = labels >= 0
    if not covered.any():
        return np.full((n_bands, h, w), np.nan, np.float32)

    masks = np.stack([(labels == k) & valid[k] for k in range(n_views)]).astype(np.float32)
    # 有效但未被任何标号选中的格网（图割留下的边角）交给最近的有效视角兜底
    orphan = covered & (masks.sum(axis=0) < 0.5)
    if orphan.any():
        first = np.argmax(valid, axis=0)
        for k in range(n_views):
            masks[k][orphan & (first == k) & valid[k]] = 1.0

    # 无效处用该视角的有效均值填充，避免 nan 污染金字塔；权重为 0 不会被采纳
    filled = np.empty_like(stack.pixels)
    for k in range(n_views):
        for b in range(n_bands):
            layer = stack.pixels[k, b]
            ok = np.isfinite(layer)
            fill = float(layer[ok].mean()) if ok.any() else 0.0
            filled[k, b] = np.where(ok, layer, fill)

    mask_pyr = [laplacian_pyramid(masks[k][None], levels) for k in range(n_views)]
    # 掩膜要的是高斯金字塔（逐级平滑），不是拉普拉斯；这里从高斯序列重建
    gauss_masks = []
    for k in range(n_views):
        g = [masks[k][None]]
        for _ in range(len(mask_pyr[k]) - 1):
            g.append(_pyr_down(g[-1]))
        gauss_masks.append(g)

    img_pyr = [laplacian_pyramid(filled[k], levels) for k in range(n_views)]
    n_levels = min(len(p) for p in img_pyr)

    blended = []
    for lv in range(n_levels):
        num = np.zeros_like(img_pyr[0][lv])
        den = np.zeros(img_pyr[0][lv].shape[-2:], np.float32)
        for k in range(n_views):
            m = gauss_masks[k][lv][0]
            num += img_pyr[k][lv] * m
            den += m
        blended.append(num / np.maximum(den, 1e-6))

    out = collapse_pyramid(blended)
    return np.where(covered, out, np.nan).astype(np.float32)


def seam_step(mosaic: np.ndarray, labels: np.ndarray) -> float:
    """接缝两侧的平均灰度台阶，用来量化融合效果。越小越好。"""
    from ms_mosaic.seamline import seam_edges

    edge = seam_edges(labels)
    if not edge.any():
        return 0.0
    inten = np.nanmean(mosaic, axis=0) if mosaic.ndim == 3 else mosaic
    steps = []
    for axis in (0, 1):
        a = labels[:-1, :] if axis == 0 else labels[:, :-1]
        b = labels[1:, :] if axis == 0 else labels[:, 1:]
        ia = inten[:-1, :] if axis == 0 else inten[:, :-1]
        ib = inten[1:, :] if axis == 0 else inten[:, 1:]
        cut = (a != b) & (a >= 0) & (b >= 0) & np.isfinite(ia) & np.isfinite(ib)
        if cut.any():
            steps.append(np.abs(ia[cut] - ib[cut]))
    return float(np.concatenate(steps).mean()) if steps else 0.0
