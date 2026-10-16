"""拼接线：用图割（alpha-expansion）为每个正射格网选一个来源视角。

问题形式
--------
重叠区每个格网都有多个视角可选。把「选哪个视角」看成一个多标号问题，
能量由两部分组成（Boykov-Veksler-Zabih, PAMI 2001 的标准形式）：

    E(L) = Σ_p D(p, L(p))  +  Σ_{p~q} V(p, q, L(p), L(q))

数据项 D 取视角质量分的补：正下视、靠近像幅中心的视角优先。
平滑项 V 取 Kwatra 等人 (SIGGRAPH 2003) 的「接缝可见度」代价：

    V(p,q,A,B) = |I_A(p) − I_B(p)| + |I_A(q) − I_B(q)|

也就是说，只在两个视角看起来本来就一样的地方下刀 —— 这正是「最优拼接线」
的含义，比按几何中线或泰森多边形切要好得多。

为什么自己写 alpha-expansion
---------------------------
现成的 aexpansion_grid 只支持「代价仅依赖标号」的 Potts 型平滑项，
而接缝代价必须依赖位置（不同地方两视角的差异不同）。所以这里直接按
BVZ 的图构造，用 maxflow 解每一步 α 扩张。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ms_mosaic.ortho import WarpedStack

INVALID_COST = 1e6
SMOOTH_WEIGHT = 0.08
MAX_CYCLES = 3
GRADIENT_WEIGHT = 0.5


@dataclass
class SeamConfig:
    smooth_weight: float = SMOOTH_WEIGHT
    max_cycles: int = MAX_CYCLES
    gradient_weight: float = GRADIENT_WEIGHT
    invalid_cost: float = INVALID_COST


def data_cost(stack: WarpedStack, cfg: SeamConfig) -> np.ndarray:
    """数据项 (n_views, h, w)：视角质量越高代价越低，无效视角给一个极大值。"""
    scores = stack.scores
    valid = np.isfinite(scores)
    if not valid.any():
        return np.full(scores.shape, cfg.invalid_cost, np.float64)
    lo, hi = np.nanmin(scores), np.nanmax(scores)
    norm = (scores - lo) / max(hi - lo, 1e-9)
    return np.where(valid, 1.0 - norm, cfg.invalid_cost).astype(np.float64)


def _view_intensity(stack: WarpedStack) -> np.ndarray:
    """各视角的灰度（多波段取均值），用于算接缝代价。"""
    px = stack.pixels
    if px.shape[1] == 1:
        return px[:, 0]
    with np.errstate(all="ignore"):
        return np.nanmean(px, axis=1)


def seam_cost_volume(stack: WarpedStack, cfg: SeamConfig) -> np.ndarray:
    """逐视角对的接缝代价 (n_views, n_views, h, w) = |I_A − I_B| 及其梯度项。

    除了灰度差，再加一项梯度差：纹理边缘处即使灰度凑巧接近，接缝也容易
    被看出来，加梯度项能把刀口推到平坦、均匀的区域。
    """
    inten = _view_intensity(stack)
    n = inten.shape[0]
    gy, gx = np.gradient(np.nan_to_num(inten, nan=0.0), axis=(1, 2))
    grad = np.hypot(gx, gy)
    diff = np.abs(inten[:, None] - inten[None, :])
    gdiff = np.abs(grad[:, None] - grad[None, :])
    cost = diff + cfg.gradient_weight * gdiff
    # 任一视角无效处给大代价，逼图割不要把接缝放在那里
    bad = ~np.isfinite(cost)
    scale = float(np.nanmax(cost)) if np.isfinite(cost).any() else 1.0
    return np.where(bad, 4.0 * max(scale, 1.0), cost).astype(np.float64)


def _expansion_move(
    labels: np.ndarray,
    alpha: int,
    data: np.ndarray,
    seam: np.ndarray,
    weight: float,
) -> tuple[np.ndarray, bool]:
    """一次 α 扩张：每个格网要么保持当前标号，要么改成 α。

    按 BVZ 的构造建图：
      - 相邻两格当前标号相同时，直接连一条容量为 V(·,·,l,α) 的边；
      - 标号不同时，插一个辅助结点，三条边分别对应
        「左改右不改」「右改左不改」「都不改」三种切法的代价。
    """
    import maxflow

    h, w = labels.shape
    n = h * w
    g = maxflow.Graph[float]()
    ids = np.arange(n, dtype=np.int64)
    g.add_nodes(n)

    lab = labels.ravel()
    # 约定：结点落在源侧 = 取 α，落在汇侧 = 保持原标号。
    # 最小割里 SOURCE→p 边在 p 落汇侧时被割，故其容量应为「保持」的代价；
    # p→SINK 边在 p 落源侧时被割，故其容量应为「取 α」的代价。
    cost_alpha = data[alpha].ravel()
    cost_keep = data[lab, ids // w, ids % w]
    is_alpha = lab == alpha
    # 已经是 α 的结点钉在源侧：给「保持」一侧一个大代价即可。
    # 用有限大值而非 inf，inf 会让最大流的数值累加失去意义。
    big = 10.0 * float(np.max(data)) + 1.0
    g.add_grid_tedges(ids, np.where(is_alpha, big, cost_keep), cost_alpha)

    flat = np.arange(n).reshape(h, w)
    rows_of = ids // w
    cols_of = ids % w

    def pair_cost(l1: np.ndarray, l2: np.ndarray, pa: np.ndarray, pb: np.ndarray) -> np.ndarray:
        """V(p,q,l1,l2)，取两端之和的一半，与另一方向合起来正好是完整代价。"""
        return weight * 0.5 * (
            seam[l1, l2, rows_of[pa], cols_of[pa]] + seam[l1, l2, rows_of[pb], cols_of[pb]]
        )

    n_aux = 0
    for axis in (0, 1):
        if axis == 0:
            pa, pb = flat[:-1].ravel(), flat[1:].ravel()
        else:
            pa, pb = flat[:, :-1].ravel(), flat[:, 1:].ravel()
        la, lb = lab[pa], lab[pb]

        same = np.nonzero(la == lb)[0]
        if same.size:
            sa, sb = pa[same], pb[same]
            cap = pair_cost(la[same], np.full(same.size, alpha), sa, sb)
            g.add_edges(sa, sb, cap, cap)

        diff = np.nonzero(la != lb)[0]
        if diff.size:
            da, db = pa[diff], pb[diff]
            l_a, l_b = la[diff], lb[diff]
            alpha_i = np.full(diff.size, alpha)
            aux = np.arange(n + n_aux, n + n_aux + diff.size, dtype=np.int64)
            g.add_nodes(diff.size)
            n_aux += diff.size
            c_a = pair_cost(l_a, alpha_i, da, db)
            c_b = pair_cost(alpha_i, l_b, da, db)
            g.add_edges(da, aux, c_a, c_a)
            g.add_edges(aux, db, c_b, c_b)
            # 辅助结点接源端，容量为 V(l_p, l_q)：仅当两端都保持原标号（都落汇侧）
            # 时这条边才被割。若错接汇端，两端都取 α 时反而会被收取 V(l_p,l_q)，
            # 而正确代价是 V(α,α)=0，整个能量就完全错了。
            g.add_grid_tedges(aux, pair_cost(l_a, l_b, da, db), np.zeros(diff.size))

    g.maxflow()
    seg = np.array([g.get_segment(int(i)) for i in ids])
    # segment 0 = 源侧 = 取 α
    new = np.where(seg == 0, alpha, lab).reshape(h, w)
    return new, bool((new != labels).any())


def optimal_labels(stack: WarpedStack, cfg: SeamConfig | None = None) -> np.ndarray:
    """求每个格网的最优来源视角。返回 (h, w) 的标号，-1 表示无任何视角可用。"""
    cfg = cfg or SeamConfig()
    n_views = stack.scores.shape[0]
    if n_views == 0:
        return np.full(stack.scores.shape[1:], -1, np.int16)
    data = data_cost(stack, cfg)
    any_valid = np.isfinite(stack.scores).any(axis=0)
    if n_views == 1:
        return np.where(any_valid, 0, -1).astype(np.int16)

    labels = np.argmin(data, axis=0).astype(np.int64)
    seam = seam_cost_volume(stack, cfg)

    for _ in range(cfg.max_cycles):
        changed = False
        for alpha in range(n_views):
            labels, moved = _expansion_move(labels, alpha, data, seam, cfg.smooth_weight)
            changed |= moved
        if not changed:
            break
    return np.where(any_valid, labels, -1).astype(np.int16)


def energy(labels: np.ndarray, stack: WarpedStack, cfg: SeamConfig | None = None) -> float:
    """给定标号的总能量，用于验证图割确实在下降。"""
    cfg = cfg or SeamConfig()
    data = data_cost(stack, cfg)
    seam = seam_cost_volume(stack, cfg)
    ok = labels >= 0
    if not ok.any():
        return 0.0
    rr, cc = np.nonzero(ok)
    total = float(data[labels[ok], rr, cc].sum())
    for axis in (0, 1):
        a = labels[:-1, :] if axis == 0 else labels[:, :-1]
        b = labels[1:, :] if axis == 0 else labels[:, 1:]
        cut = (a != b) & (a >= 0) & (b >= 0)
        if not cut.any():
            continue
        ra, ca = np.nonzero(cut)
        rb, cb = (ra + 1, ca) if axis == 0 else (ra, ca + 1)
        la, lb = a[ra, ca], b[ra, ca]
        total += cfg.smooth_weight * 0.5 * float(
            (seam[la, lb, ra, ca] + seam[la, lb, rb, cb]).sum()
        )
    return total


def seam_edges(labels: np.ndarray) -> np.ndarray:
    """标号发生变化的位置，即拼接线本身。用于导出矢量与质量报告插图。"""
    edge = np.zeros(labels.shape, bool)
    edge[:-1, :] |= (labels[:-1, :] != labels[1:, :]) & (labels[:-1, :] >= 0) & (labels[1:, :] >= 0)
    edge[1:, :] |= edge[:-1, :]
    edge[:, :-1] |= (labels[:, :-1] != labels[:, 1:]) & (labels[:, :-1] >= 0) & (labels[:, 1:] >= 0)
    edge[:, 1:] |= edge[:, :-1]
    return edge


def compose(stack: WarpedStack, labels: np.ndarray) -> np.ndarray:
    """按标号取像素，得到未做辐射融合的拼接结果。"""
    n_views, n_bands, h, w = stack.pixels.shape
    out = np.full((n_bands, h, w), np.nan, np.float32)
    if n_views == 0:
        return out
    ok = labels >= 0
    rr, cc = np.nonzero(ok)
    lab = labels[ok]
    for b in range(n_bands):
        out[b][ok] = stack.pixels[lab, b, rr, cc]
    return out
