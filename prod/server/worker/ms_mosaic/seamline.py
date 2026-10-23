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
    # 人工指定优先影像（全局影像号）。有效处数据项低于其余视角，图割轮数不变。
    # Boykov & Jolly, ICCV 2001：用户约束进数据项，平滑项仍是接缝可见度。
    prefer_ids: tuple[int, ...] = ()
    prefer_factor: float = 0.25
    prefer_margin: float = 1.0
    # 人工圈定的来源影像，(h, w)，-1 表示仍由图割决定。Boykov & Jolly, ICCV 2001。
    constraint: np.ndarray | None = None


def data_cost(stack: WarpedStack, cfg: SeamConfig) -> np.ndarray:
    """数据项 (n_views, h, w)：视角质量越高代价越低，无效视角给一个极大值。"""
    scores = stack.scores
    valid = np.isfinite(scores)
    if not valid.any():
        return np.full(scores.shape, cfg.invalid_cost, np.float64)
    lo, hi = np.nanmin(scores), np.nanmax(scores)
    norm = (scores - lo) / max(hi - lo, 1e-9)
    cost = np.where(valid, 1.0 - norm, cfg.invalid_cost).astype(np.float64)
    wanted = {int(i) for i in cfg.prefer_ids}
    if wanted:
        # 优先影像之间仍按质量分排序；非优先影像整体抬高，有效处不会压过优先影像。
        # 平滑项不改，接缝仍可以躲开两张影像差异大的位置。
        for k, vid in enumerate(stack.views):
            if int(vid) in wanted:
                cost[k] = np.where(valid[k], cost[k] * float(cfg.prefer_factor), cost[k])
            else:
                cost[k] = np.where(valid[k], cost[k] + float(cfg.prefer_margin), cost[k])
    forced = cfg.constraint
    if forced is not None:
        own_valid = np.zeros(forced.shape, dtype=bool)
        for k, vid in enumerate(stack.views):
            own = (forced == int(vid)) & valid[k]
            own_valid |= own
            cost[k] = np.where(own, 0.0, cost[k])
        for k, vid in enumerate(stack.views):
            ban = own_valid & (forced != int(vid))
            cost[k] = np.where(ban, cfg.invalid_cost, cost[k])
    return cost


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


class SeamCost:
    """按需取值的接缝代价，seam[l1, l2, r, c] 与 seam_cost_volume 逐位相同。

    整卷是 n²·h·w 个 float64：12 视角 480² 块连中间量约 1 GB，而 α 扩张只读相邻格对上的值。
    """

    def __init__(self, stack: WarpedStack, cfg: SeamConfig) -> None:
        self.inten = _view_intensity(stack)
        gy, gx = np.gradient(np.nan_to_num(self.inten, nan=0.0), axis=(1, 2))
        self.grad = np.hypot(gx, gy)
        self.gw = cfg.gradient_weight
        n = self.inten.shape[0]
        scale = None
        for a in range(n):
            for b in range(a, n):
                c = self._raw(a, b, slice(None), slice(None))
                if np.isfinite(c).any():
                    m = float(np.nanmax(c))
                    scale = m if scale is None else max(scale, m)
        self.fill = 4.0 * max(1.0 if scale is None else scale, 1.0)

    def _raw(self, l1, l2, r, c) -> np.ndarray:
        diff = np.abs(self.inten[l1, r, c] - self.inten[l2, r, c])
        gdiff = np.abs(self.grad[l1, r, c] - self.grad[l2, r, c])
        return diff + self.gw * gdiff

    def __getitem__(self, key) -> np.ndarray:
        cost = self._raw(*key)
        return np.where(np.isfinite(cost), cost, self.fill).astype(np.float64)

    @property
    def upper(self) -> float:
        """任意 seam[l1, l2, r, c] 的上界：有限值不超过 scale，非有限处取 fill ≥ scale。"""
        return self.fill


def _seam_upper(seam) -> float:
    return float(seam.upper) if isinstance(seam, SeamCost) else float(np.max(seam))


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

    固定结点消元（Kohli & Torr, PAMI 2007；Alahari 等, PAMI 2010）：
      - 已是 α 的格网扩张后仍是 α；
      - 取 α 的数据项比保持高出 4·V_max 以上的格网，在最优扩张里一定保持
        （若取了 α，单独改回能量至少降 gap − 4·V_max > 0，与最优矛盾）。
    这两类不进图，它们与自由格网之间的边折成自由格网的 t 边。能量与原图逐项相同，
    最小割的解集合不变，只是图小得多（无效视角区与已是 α 的区域不再求流）。
    """
    import maxflow

    h, w = labels.shape
    n = h * w
    lab = labels.ravel()
    ids = np.arange(n, dtype=np.int64)
    rows_of = ids // w
    cols_of = ids % w
    cost_alpha = data[alpha].ravel()
    cost_keep = data[lab, rows_of, cols_of]
    is_alpha = lab == alpha
    v_max = weight * _seam_upper(seam)
    free = ~is_alpha & ~(cost_alpha - cost_keep > 4.0 * v_max)
    m = int(free.sum())
    if m == 0:
        return labels, False
    node = np.full(n, -1, np.int64)
    node[free] = np.arange(m)
    # 固定结点的最终标号：α 区仍是 α，其余保持
    final = np.where(is_alpha, alpha, lab)

    def pair_cost(l1: np.ndarray, l2: np.ndarray, pa: np.ndarray, pb: np.ndarray) -> np.ndarray:
        """V(p,q,l1,l2)，取两端之和的一半，与另一方向合起来正好是完整代价。

        同标号不是接缝，V(l,l)=0，与 energy 一致；否则无效像元处 seam[l,l] 取填充值，
        V 不再是度量，BVZ 构造就不等价于所求能量。
        """
        cost = weight * 0.5 * (
            seam[l1, l2, rows_of[pa], cols_of[pa]] + seam[l1, l2, rows_of[pb], cols_of[pb]]
        )
        return np.where(l1 == l2, 0.0, cost)

    # 约定：结点落在源侧 = 取 α，落在汇侧 = 保持原标号。
    # SOURCE→p 在 p 落汇侧时被割，容量是「保持」的代价；p→SINK 在 p 落源侧时被割，容量是「取 α」的代价。
    src = cost_keep[free].astype(np.float64)
    snk = cost_alpha[free].astype(np.float64)
    g = maxflow.Graph[float]()
    g.add_nodes(m)

    flat = ids.reshape(h, w)
    n_aux = 0
    for axis in (0, 1):
        if axis == 0:
            pa, pb = flat[:-1].ravel(), flat[1:].ravel()
        else:
            pa, pb = flat[:, :-1].ravel(), flat[:, 1:].ravel()
        fa, fb = free[pa], free[pb]

        # 一端固定：固定端标号 lf 已定，自由端保持付 V(l, lf)，取 α 付 V(α, lf)
        for p_free, p_fix in ((pa[fa & ~fb], pb[fa & ~fb]), (pb[fb & ~fa], pa[fb & ~fa])):
            if not p_free.size:
                continue
            lf = final[p_fix]
            alpha_i = np.full(p_free.size, alpha)
            k = node[p_free]
            np.add.at(src, k, pair_cost(lab[p_free], lf, p_free, p_fix))
            np.add.at(snk, k, pair_cost(alpha_i, lf, p_free, p_fix))

        both = fa & fb
        pa, pb = pa[both], pb[both]
        la, lb = lab[pa], lab[pb]
        same = np.nonzero(la == lb)[0]
        if same.size:
            sa, sb = pa[same], pb[same]
            cap = pair_cost(la[same], np.full(same.size, alpha), sa, sb)
            g.add_edges(node[sa], node[sb], cap, cap)

        diff = np.nonzero(la != lb)[0]
        if diff.size:
            da, db = pa[diff], pb[diff]
            l_a, l_b = la[diff], lb[diff]
            alpha_i = np.full(diff.size, alpha)
            aux = np.arange(m + n_aux, m + n_aux + diff.size, dtype=np.int64)
            g.add_nodes(diff.size)
            n_aux += diff.size
            c_a = pair_cost(l_a, alpha_i, da, db)
            c_b = pair_cost(alpha_i, l_b, da, db)
            g.add_edges(node[da], aux, c_a, c_a)
            g.add_edges(aux, node[db], c_b, c_b)
            # 辅助结点接源端，容量为 V(l_p, l_q)：仅当两端都保持原标号（都落汇侧）
            # 时这条边才被割。若错接汇端，两端都取 α 时反而会被收取 V(l_p,l_q)，
            # 而正确代价是 V(α,α)=0，整个能量就完全错了。
            g.add_grid_tedges(aux, pair_cost(l_a, l_b, da, db), np.zeros(diff.size))

    g.add_grid_tedges(np.arange(m), src, snk)
    g.maxflow()
    # get_grid_segments：True = 汇侧 = 保持；False = 源侧 = 取 α
    keep = g.get_grid_segments(np.arange(m))
    new = final.copy()
    new[np.flatnonzero(free)[~keep]] = alpha
    new = new.reshape(h, w)
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
    seam = SeamCost(stack, cfg)

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
    seam = SeamCost(stack, cfg)
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
