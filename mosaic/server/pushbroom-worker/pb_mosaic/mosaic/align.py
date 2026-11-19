"""航带间影像配准：补偿各航带 GPS 绝对位置的不一致。

非 RTK 的 GPS 在不同航带之间常差一两米，接缝处地物错开。做法：
1. 每条航带单独用一个波段成图，有重叠的航带对做带通滤波后的
   归一化互相关（NCC），保留前几个相关峰作为候选平移。
   镶嵌时重叠区取离各自中线最近的航带，看得见的接缝只在相邻航带中间。
   航带内有随旁向位置变化的残余形变（整条平移消不掉），所以只在接缝两侧
   一窄条里量：量到的就是接缝处要的平移，相关性也比全重叠区高得多。
   不形成接缝的航带对（隔一条的同向航带）只用全重叠区量，权重很低，仅用于连通。
2. 最小二乘解每条航带的平移量（b − a = 平移），按相关性加权，
   每个连通块内平移总和为零，整体仍以 GPS 为准。
3. 农田、果园这类重复纹理会有好几个差不多高的峰。每轮按当前解给出的预测，
   每对航带改选离预测最近、相关性不太差的候选；对不上预测的航带对不参与解算。
   有闭合环（同向航带也有重叠）时，这一步能把认错的峰纠正过来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.geom.grid import OutputGrid, grid_from_bounds
from pb_mosaic.mosaic.merge import Cancelled, strip_boxes

log = logging.getLogger("pb_mosaic.align")

# 带通的两个高斯尺度（像元）：小的去掉像元噪声，大的去掉光照和亮度差
BAND_PASS_SIGMA_PX = (1.0, 10.0)
# 每对航带保留几个候选峰
CANDIDATES = 4
# 「选候选 → 解算」最多迭代几轮
SELECT_ROUNDS = 6
# 一对航带配准时的格网像元上限，超过就放粗分辨率，控制内存（每像元约 60 字节的中间数组）
MAX_PAIR_PIXELS = 6_000_000

# 接缝两侧各取多宽来量平移
SEAM_HALF_WIDTH_M = 4.0
# 接缝带面积不到这么多就当这对航带不形成接缝
MIN_SEAM_M2 = 300.0
# 不形成接缝的航带对在解算里的权重系数
NON_SEAM_WEIGHT = 0.05

# 读某条航带配准波段的函数：(起始行, 结束行) → (行, 像元) 数组
BandReader = Callable[[int, int], np.ndarray]


@dataclass
class AlignParams:
    enabled: bool = True
    gsd_m: float = 0.1
    max_shift_m: float = 5.0
    min_ncc: float = 0.15
    # 候选峰的相关性不能比最高峰低太多
    candidate_tolerance: float = 0.15
    min_overlap_m2: float = 500.0
    max_residual_m: float = 0.6


@dataclass(frozen=True)
class Candidate:
    east: float  # b 航带要平移 (east, north) 米才能对上 a
    north: float
    ncc: float


@dataclass
class PairShift:
    a: int
    b: int
    overlap_m2: float
    candidates: list[Candidate]
    gsd_m: float = 0.0
    seam_m2: float = 0.0
    chosen: int = 0
    used: bool = False
    residual_m: float | None = None
    reason: str = ""

    @property
    def best(self) -> Candidate:
        return self.candidates[self.chosen]

    @property
    def seam(self) -> bool:
        return self.seam_m2 >= MIN_SEAM_M2

    @property
    def weight(self) -> float:
        return max(self.best.ncc, 1e-3) ** 2 * (1.0 if self.seam else NON_SEAM_WEIGHT)

    def as_dict(self) -> dict:
        c = self.best if self.candidates else Candidate(0.0, 0.0, -1.0)
        return {
            "strips": [self.a, self.b],
            "shift_east_north_m": [round(c.east, 3), round(c.north, 3)],
            "ncc": round(c.ncc, 4),
            "peak_rank": self.chosen + 1,
            "overlap_m2": round(self.overlap_m2, 1),
            "seam": self.seam,
            "seam_m2": round(self.seam_m2, 1),
            "gsd_m": round(self.gsd_m, 3),
            "used": self.used,
            "residual_m": None if self.residual_m is None else round(self.residual_m, 3),
            "reason": self.reason,
        }


@dataclass
class AlignResult:
    offsets: dict[int, tuple[float, float]] = field(default_factory=dict)
    pairs: list[PairShift] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "offsets_east_north_m": {str(k): [round(v[0], 3), round(v[1], 3)] for k, v in self.offsets.items()},
            "pairs": [p.as_dict() for p in self.pairs],
            "note": self.note,
        }


def render_strip(
    geom: StripGeometry, grid: OutputGrid, plane: np.ndarray, line0: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """一条航带的单波段最近邻成图。plane[k] 是原始第 line0 + k 行。返回 (图, 有效掩膜)。"""
    img, dist = _render(geom, grid, plane, line0)
    return img, np.isfinite(dist)


def center_distance(geom: StripGeometry, grid: OutputGrid) -> np.ndarray:
    """每个格网点离这条航带狭缝中心多少米（旁向），航带外为 inf。镶嵌按它选航带。"""
    return _render(geom, grid, None)[1]


def _render(
    geom: StripGeometry, grid: OutputGrid, plane: np.ndarray | None, line0: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    box = strip_boxes([geom], grid)[0]
    img = np.zeros((grid.height, grid.width), dtype=np.float32)
    dist = np.full(img.shape, np.inf, dtype=np.float32)
    if box.r0 >= box.r1 or box.c0 >= box.c1:
        return img, dist
    qx, qy = np.meshgrid(grid.col_x(np.arange(box.c0, box.c1)), grid.row_y(np.arange(box.r0, box.r1)))
    li, si, good = geom.invert(qx.ravel(), qy.ravel())
    d = np.abs(si - geom.sensor.center_px) * geom.sensor.gsd_cross_m
    if plane is not None:
        li = np.clip(np.rint(li), 0, geom.n - 1).astype(np.int64) + geom.strip.line_start - line0
        si = np.clip(np.rint(si), 0, geom.sensor.samples - 1).astype(np.int64)
        good &= (li >= 0) & (li < plane.shape[0])
        sub = np.zeros(qx.size, dtype=np.float32)
        sub[good] = plane[li[good], si[good]]
        img[box.r0 : box.r1, box.c0 : box.c1] = sub.reshape(qx.shape)
    dist[box.r0 : box.r1, box.c0 : box.c1] = np.where(good, d, np.inf).reshape(qx.shape)
    return img, dist


def bandpass(x: np.ndarray, m: np.ndarray) -> np.ndarray:
    """掩膜内的高斯带通，掩膜外为 0。"""
    from scipy.ndimage import gaussian_filter

    mf = m.astype(np.float64)
    xf = np.where(m, x, 0.0).astype(np.float64)

    def blur(s: float) -> np.ndarray:
        return gaussian_filter(xf, s) / np.maximum(gaussian_filter(mf, s), 1e-6)

    return np.where(m, blur(BAND_PASS_SIGMA_PX[0]) - blur(BAND_PASS_SIGMA_PX[1]), 0.0)


@dataclass(frozen=True)
class Peak:
    drow: int
    dcol: int
    ncc: float


def ncc_search(
    ref: np.ndarray,
    ref_ok: np.ndarray,
    tpl: np.ndarray,
    tpl_ok: np.ndarray,
    max_px: int,
    *,
    min_cover: float = 0.3,
    n_peaks: int = 1,
) -> tuple[list[Peak], int]:
    """tpl 平移 (drow, dcol) 后与 ref 的归一化互相关；只在两边都有数据处计算。

    返回按相关性从高到低的前 n_peaks 个局部极大值（不含搜索边界上的），以及模板像元数。
    """
    from scipy.ndimage import maximum_filter
    from scipy.signal import fftconvolve

    rows, cols = np.nonzero(tpl_ok)
    if rows.size == 0:
        return [], 0
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    tm = tpl_ok[r0:r1, c0:c1].astype(np.float64)
    t = bandpass(tpl[r0:r1, c0:c1], tpl_ok[r0:r1, c0:c1])
    sr0, sc0 = max(0, r0 - max_px), max(0, c0 - max_px)
    sr1, sc1 = min(ref.shape[0], r1 + max_px), min(ref.shape[1], c1 + max_px)
    if sr1 - sr0 < r1 - r0 or sc1 - sc0 < c1 - c0:
        return [], int(tm.sum())
    sm = ref_ok[sr0:sr1, sc0:sc1].astype(np.float64)
    s = bandpass(ref[sr0:sr1, sc0:sc1], ref_ok[sr0:sr1, sc0:sc1])

    def xc(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return fftconvolve(a, b[::-1, ::-1], mode="valid")

    num = xc(s, t)
    e_ref = xc(s * s, tm)
    e_tpl = xc(sm, t * t)
    cover = xc(sm, tm)
    ncc = num / np.sqrt(np.maximum(e_ref * e_tpl, 1e-12))
    ncc[cover < min_cover * tm.sum()] = -1.0
    peak = (ncc == maximum_filter(ncc, size=5, mode="nearest")) & (ncc > -1.0)
    # 落在搜索范围边上的峰说明真值可能在范围外，不可信
    peak[[0, -1], :] = False
    peak[:, [0, -1]] = False
    pr, pc = np.nonzero(peak)
    order = np.argsort(ncc[pr, pc])[::-1][:n_peaks]
    out = [Peak(int(sr0 + pr[k] - r0), int(sc0 + pc[k] - c0), float(ncc[pr[k], pc[k]])) for k in order]
    return out, int(tm.sum())


def _components(ids: list[int], pairs: list[PairShift]) -> list[list[int]]:
    index = {sid: k for k, sid in enumerate(ids)}
    parent = list(range(len(ids)))

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for p in pairs:
        if p.used:
            parent[find(index[p.a])] = find(index[p.b])
    groups: dict[int, list[int]] = {}
    for k in range(len(ids)):
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


def _solve(ids: list[int], pairs: list[PairShift]) -> dict[int, tuple[float, float]]:
    """t_b − t_a = 平移，按 ncc²（非接缝对再乘小系数）加权最小二乘；每个连通块内平移总和为零。"""
    index = {sid: k for k, sid in enumerate(ids)}
    rows: list[np.ndarray] = []
    rhs: list[np.ndarray] = []
    for p in pairs:
        if not p.used:
            continue
        c = p.best
        w = p.weight
        r = np.zeros(len(ids))
        r[index[p.b]], r[index[p.a]] = w, -w
        rows.append(r)
        rhs.append(np.array([c.east, c.north]) * w)
    for members in _components(ids, pairs):
        r = np.zeros(len(ids))
        r[members] = 1.0
        rows.append(r)
        rhs.append(np.zeros(2))
    sol = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    return {sid: (float(sol[index[sid], 0]), float(sol[index[sid], 1])) for sid in ids}


def _residual(p: PairShift, c: Candidate, offsets: dict[int, tuple[float, float]]) -> float:
    ta, tb = offsets[p.a], offsets[p.b]
    return float(np.hypot(tb[0] - ta[0] - c.east, tb[1] - ta[1] - c.north))


def _select(pairs: list[PairShift], ids: list[int], params: AlignParams) -> dict[int, tuple[float, float]]:
    """交替「解算」和「按预测改选候选峰」，直到选择不再变化。"""
    for p in pairs:
        p.chosen, p.used = 0, bool(p.candidates) and not p.reason
    offsets = {sid: (0.0, 0.0) for sid in ids}
    for _ in range(SELECT_ROUNDS):
        if not any(p.used for p in pairs):
            break
        offsets = _solve(ids, pairs)
        # 最差的一对先拿掉再解，免得一个认错的峰把整体拉偏；先拿不形成接缝的
        bad = [p for p in pairs if p.used and _residual(p, p.best, offsets) > params.max_residual_m]
        if bad and sum(p.used for p in pairs) > 1:
            worst = max(bad, key=lambda p: (not p.seam, _residual(p, p.best, offsets)))
            worst.used = False
            offsets = _solve(ids, pairs)
        changed = False
        for p in pairs:
            if not p.candidates or p.reason:
                continue
            top = p.candidates[0].ncc
            ok = [k for k, c in enumerate(p.candidates) if c.ncc >= top - params.candidate_tolerance]
            k = min(ok, key=lambda k: _residual(p, p.candidates[k], offsets))
            used = _residual(p, p.candidates[k], offsets) <= params.max_residual_m
            if (k, used) != (p.chosen, p.used):
                p.chosen, p.used, changed = k, used, True
        if not changed:
            break
    if any(p.used for p in pairs):
        offsets = _solve(ids, pairs)
    for p in pairs:
        if not p.candidates or p.reason:
            continue
        p.residual_m = _residual(p, p.best, offsets)
        if not p.used:
            p.reason = (
                f"与其它航带对的结果不一致（残差 {p.residual_m:.2f} m）"
                if p.seam
                else f"不形成接缝，与接缝配准结果相差 {p.residual_m:.2f} m（航带内旁向残余形变），未采用"
            )
    return offsets


def _intersect(a: tuple, b: tuple) -> tuple[float, float, float, float] | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _measure_pair(
    ga: StripGeometry,
    gb: StripGeometry,
    others: list[StripGeometry],
    box: tuple,
    read_band: BandReader,
    epsg: int,
    params: AlignParams,
) -> PairShift:
    pad = params.max_shift_m
    w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
    gsd = max(params.gsd_m, float(np.sqrt(w * h / MAX_PAIR_PIXELS)))
    grid = grid_from_bounds(epsg, box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad, gsd)
    imgs = []
    for g in (ga, gb):
        l0, l1 = g.strip.line_start, g.strip.line_end
        imgs.append(_render(g, grid, read_band(l0, l1), line0=l0))
    (a, da), (bb, db) = imgs
    ma = np.isfinite(da)
    both = ma & np.isfinite(db)
    px_area = gsd * gsd
    p = PairShift(ga.strip.index, gb.strip.index, overlap_m2=float(both.sum()) * px_area, candidates=[], gsd_m=gsd)
    if p.overlap_m2 < params.min_overlap_m2:
        p.reason = f"重叠只有 {p.overlap_m2:.0f} m²"
        return p
    # 接缝：两条航带离中线一样近，且没有第三条航带更近
    nearest_other = np.full(a.shape, np.inf, dtype=np.float32)
    for g in others:
        if _intersect(g.bounds(), box) is not None:
            nearest_other = np.minimum(nearest_other, center_distance(g, grid))
    with np.errstate(invalid="ignore"):
        seam = both & (np.abs(da - db) <= 2 * SEAM_HALF_WIDTH_M) & (np.minimum(da, db) < nearest_other)
    p.seam_m2 = float(seam.sum()) * px_area
    tpl_ok = seam if p.seam else both
    max_px = max(2, int(round(params.max_shift_m / gsd)))
    peaks, _ = ncc_search(a, ma, bb, tpl_ok, max_px, n_peaks=CANDIDATES)
    p.candidates = [Candidate(pk.dcol * gsd, -pk.drow * gsd, pk.ncc) for pk in peaks if pk.ncc >= params.min_ncc]
    if not p.candidates:
        top = peaks[0].ncc if peaks else -1.0
        p.reason = f"相关性 {top:.2f} 低于 {params.min_ncc}，或平移超出 {params.max_shift_m} m"
    return p


def align_strips(
    geoms: list[StripGeometry],
    read_band: BandReader,
    epsg: int,
    params: AlignParams,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> AlignResult:
    ids = [g.strip.index for g in geoms]
    result = AlignResult(offsets={sid: (0.0, 0.0) for sid in ids})
    if not params.enabled:
        result.note = "未启用航带间配准"
        return result
    if len(geoms) < 2:
        result.note = "只有一条航带，无需配准"
        return result
    bounds = [g.bounds() for g in geoms]
    todo = []
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            box = _intersect(bounds[i], bounds[j])
            if box is not None and (box[2] - box[0]) * (box[3] - box[1]) >= params.min_overlap_m2:
                todo.append((i, j, box))
    for k, (i, j, box) in enumerate(todo):
        if cancelled and cancelled():
            raise Cancelled()
        if progress:
            progress(k / len(todo), f"航带 {ids[i]} 与 {ids[j]} 配准（{k + 1}/{len(todo)}）")
        others = [g for k, g in enumerate(geoms) if k not in (i, j)]
        p = _measure_pair(geoms[i], geoms[j], others, box, read_band, epsg, params)
        if p.overlap_m2 >= params.min_overlap_m2:
            result.pairs.append(p)
    result.offsets = _select(result.pairs, ids, params)
    n_used = sum(p.used for p in result.pairs)
    if n_used == 0:
        result.offsets = {sid: (0.0, 0.0) for sid in ids}
        result.note = "没有可靠的航带重叠，未做修正，接缝处可能错位"
    else:
        moved = max(float(np.hypot(*v)) for v in result.offsets.values())
        seams = [p for p in result.pairs if p.seam]
        seams_used = [p for p in seams if p.used]
        alone = [sid for sid in ids if not any(p.used and sid in (p.a, p.b) for p in result.pairs)]
        result.note = f"{len(seams_used)}/{len(seams)} 条接缝参与配准"
        helpers = n_used - len(seams_used)
        if helpers:
            result.note += f"（另有 {helpers} 对非相邻航带辅助连通）"
        result.note += f"，最大修正 {moved:.2f} m"
        if seams_used:
            result.note += f"，接缝相关性中位 {float(np.median([p.best.ncc for p in seams_used])):.2f}"
        # 接缝对多于连通所需时才有多余观测，残差才有意义
        n_comp = len(_components(ids, seams_used)) if seams_used else len(ids)
        if len(seams_used) > len(ids) - n_comp:
            res = [p.residual_m for p in seams_used if p.residual_m is not None]
            result.note += f"，接缝残差中位 {float(np.median(res)):.2f} m"
        unused = [p for p in seams if not p.used]
        if unused:
            result.note += "；接缝 " + "、".join(f"{p.a}-{p.b}" for p in unused) + " 未能配准，可能错位"
        if alone:
            result.note += f"；航带 {', '.join(map(str, alone))} 没有可靠重叠，保持 GPS 位置"
    log.info("align: %s", result.note)
    return result
