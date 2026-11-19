"""航带精配准（自检校）：在整条平移之后，逐条估计狭缝剪切并细化平移。

整条平移之后，往返相邻航带的重叠区里仍有沿航向错位，而且随旁向位置线性变化
（0419 架次在 16 m 宽的重叠里从 −1 m 变到 +1 m，旁向错位都在 0.2 m 以内）。
这是狭缝与航迹不垂直造成的剪切：离中线 d 米的像元沿航向错开 φ·d 米。
φ 随航向翻转（偏流），同向航带之间也不完全一样，所以逐条估计。

1. 每对重叠航带按（a 航带旁向 × 沿航向）切块，块内小范围 NCC 量残余错位。
   搜索半径小于农田重复纹理的周期；次峰和主峰差不多高的块不用。
2. 块中心 p 处：m = C_b(p) − C_a(p)，C_i(p) = Δt_i + φ_i·d_i(p)·h_i(p)，
   d 为离中线的旁向米数，h 为航向。Huber 加权最小二乘解每条航带的 Δt 和 φ。
3. 所有航带同加一个剪切，等价于整幅图的全局剪切加各航带平移，重叠区里量不出来。
   约定往返两个方向的平均剪切互为相反数（偏流随航向翻转，安装偏角按 0 处理）；
   每个连通块内 Δt 之和为 0，整体位置仍以 GPS 为准。
4. 按修正后的几何再量一轮（搜索半径更小）细化，最后再量一轮作质检。
   修正后错位没有变小，或剪切、平移超出合理范围时整体放弃，保留整条平移的结果。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.geom.grid import grid_from_bounds
from pb_mosaic.mosaic.align import MAX_PAIR_PIXELS, SEAM_HALF_WIDTH_M, BandReader, _intersect, ncc_search, sample_strip
from pb_mosaic.mosaic.merge import Cancelled

log = logging.getLogger("pb_mosaic.refine")

# 约束行的权重（开方后），远大于观测权重
GAUGE_WEIGHT = 1e3
IRLS_ROUNDS = 10
# 修正后错位中位不到这么多个配准像元时，没变小也算对齐（本来就准的数据）
ALREADY_ALIGNED_PX = 0.5


@dataclass
class RefineParams:
    enabled: bool = True
    gsd_m: float = 0.1
    tile_cross_m: float = 2.4
    tile_along_m: float = 20.0
    # 块内两条航带都有数据的面积至少占名义块面积的比例
    min_tile_fill: float = 0.5
    # 每轮的搜索半径（米）；最后一轮只量不解，作质检
    search_m: tuple[float, ...] = (1.2, 0.5, 0.4)
    min_ncc: float = 0.3
    peak_margin: float = 0.05
    min_tiles_per_pair: int = 4
    huber_m: float = 0.25
    outlier_m: float = 0.6
    # 先验只防病态；不可观测的分量已由约束行固定，先验太紧会把窄航带的剪切压小
    prior_shift_m: float = 2.0
    prior_shear_deg: float = 30.0
    max_shear_deg: float = 8.0
    max_shift_m: float = 1.5


@dataclass
class Tile:
    a: int
    b: int
    x: float
    y: float
    da: float  # 块中心离 a、b 中线的旁向米数，各自航迹右侧为正
    db: float
    ha: tuple[float, float]
    hb: tuple[float, float]
    east: float  # b 要平移 (east, north) 米才能对上 a
    north: float
    ncc: float
    seam: bool = False

    @property
    def size(self) -> float:
        return float(np.hypot(self.east, self.north))


@dataclass
class PairStat:
    a: int
    b: int
    tiles_before: int = 0
    tiles_after: int = 0
    before_m: float | None = None  # 块内错位中位
    after_m: float | None = None
    seam: bool = False

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        return {
            "strips": [self.a, self.b],
            "seam": self.seam,
            "tiles_before": self.tiles_before,
            "tiles_after": self.tiles_after,
            "median_before_m": r(self.before_m),
            "median_after_m": r(self.after_m),
        }


@dataclass
class RefineResult:
    applied: bool = False
    # 每条航带：在整条平移之外再加的 (东, 北, 剪切弧度)
    corrections: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    pairs: list[PairStat] = field(default_factory=list)
    before_m: float | None = None
    after_m: float | None = None
    before_p90_m: float | None = None
    after_p90_m: float | None = None
    outliers: int = 0
    note: str = ""

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        return {
            "applied": self.applied,
            "corrections": {
                str(k): {"shift_east_north_m": [round(v[0], 3), round(v[1], 3)], "shear_deg": round(float(np.degrees(v[2])), 3)}
                for k, v in self.corrections.items()
            },
            "pairs": [p.as_dict() for p in self.pairs],
            "median_before_m": r(self.before_m),
            "median_after_m": r(self.after_m),
            "p90_before_m": r(self.before_p90_m),
            "p90_after_m": r(self.after_p90_m),
            "outlier_tiles": self.outliers,
            "note": self.note,
        }


def _measure_pair(
    ga: StripGeometry,
    gb: StripGeometry,
    others: list[StripGeometry],
    plane: Callable[[StripGeometry], np.ndarray],
    epsg: int,
    params: RefineParams,
    radius: float,
) -> list[Tile]:
    box = _intersect(ga.bounds(), gb.bounds())
    if box is None:
        return []
    pad = radius + 0.5
    w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
    gsd = max(params.gsd_m, float(np.sqrt(w * h / MAX_PAIR_PIXELS)))
    grid = grid_from_bounds(epsg, box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad, gsd)
    ra = sample_strip(ga, grid, plane(ga), ga.strip.line_start)
    rb = sample_strip(gb, grid, plane(gb), gb.strip.line_start)
    rows, cols = np.nonzero(ra.ok & rb.ok)
    if rows.size == 0:
        return []
    ca = ga.cross_m(ra.sample[rows, cols])
    cb = gb.cross_m(rb.sample[rows, cols])
    la, lb = ra.line[rows, cols], rb.line[rows, cols]
    along = la * float(np.median(ga.step))
    kc = np.floor(ca / params.tile_cross_m).astype(np.int64)
    ka = np.floor(along / params.tile_along_m).astype(np.int64)
    key = (kc - kc.min()) * (int(ka.max() - ka.min()) + 1) + (ka - ka.min())
    order = np.argsort(key, kind="stable")
    key = key[order]
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], key.size]
    need = params.min_tile_fill * params.tile_cross_m * params.tile_along_m / (gsd * gsd)
    max_px = max(2, int(round(radius / gsd)))
    xs, ys = grid.col_x(np.arange(grid.width)), grid.row_y(np.arange(grid.height))
    mask = np.zeros(ra.ok.shape, dtype=bool)
    tiles: list[Tile] = []
    for s0, s1 in zip(starts, ends):
        if s1 - s0 < need:
            continue
        sel = order[s0:s1]
        rr, cc = rows[sel], cols[sel]
        mask[rr, cc] = True
        peaks, _ = ncc_search(ra.img, ra.ok, rb.img, mask, max_px, n_peaks=2, min_cover=0.8, subpixel=True)
        mask[rr, cc] = False
        if not peaks or peaks[0].ncc < params.min_ncc:
            continue
        if len(peaks) > 1 and peaks[0].ncc - peaks[1].ncc < params.peak_margin:
            continue
        hax, hay = ga.heading_at(np.array([la[sel].mean()]))
        hbx, hby = gb.heading_at(np.array([lb[sel].mean()]))
        tiles.append(
            Tile(
                a=ga.strip.index,
                b=gb.strip.index,
                x=float(xs[cc].mean()),
                y=float(ys[rr].mean()),
                da=float(ca[sel].mean()),
                db=float(cb[sel].mean()),
                ha=(float(hax[0]), float(hay[0])),
                hb=(float(hbx[0]), float(hby[0])),
                east=peaks[0].dcol * gsd,
                north=-peaks[0].drow * gsd,
                ncc=peaks[0].ncc,
            )
        )
    if tiles:
        _mark_seams(tiles, others)
    return tiles


def _mark_seams(tiles: list[Tile], others: list[StripGeometry]) -> None:
    """块落在 a、b 的接缝带里：两边离中线差不多远，且没有第三条航带更近。"""
    x = np.array([t.x for t in tiles])
    y = np.array([t.y for t in tiles])
    nearest = np.full(x.size, np.inf)
    for g in others:
        _, s, ok = g.invert(x, y)
        nearest = np.minimum(nearest, np.where(ok, np.abs(g.cross_m(s)), np.inf))
    for k, t in enumerate(tiles):
        da, db = abs(t.da), abs(t.db)
        t.seam = abs(da - db) <= 2 * SEAM_HALF_WIDTH_M and min(da, db) < nearest[k]


def _components(ids: list[int], links: set[tuple[int, int]]) -> list[list[int]]:
    parent = {s: s for s in ids}

    def find(s: int) -> int:
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    for a, b in links:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for s in ids:
        groups.setdefault(find(s), []).append(s)
    return list(groups.values())


def _solve(
    tiles: list[Tile], headings: dict[int, tuple[float, float]], params: RefineParams
) -> tuple[dict[int, np.ndarray], np.ndarray, np.ndarray]:
    """返回 (每条航带的 [Δx, Δy, Δφ]，每块解算后的残差米数，是否离群)。"""
    ids = sorted({t.a for t in tiles} | {t.b for t in tiles})
    ix = {s: k for k, s in enumerate(ids)}
    n = 3 * len(ids)
    A = np.zeros((2 * len(tiles), n))
    y = np.zeros(2 * len(tiles))
    for k, t in enumerate(tiles):
        for c in (0, 1):
            r = 2 * k + c
            A[r, 3 * ix[t.b] + c] += 1.0
            A[r, 3 * ix[t.b] + 2] += t.db * t.hb[c]
            A[r, 3 * ix[t.a] + c] -= 1.0
            A[r, 3 * ix[t.a] + 2] -= t.da * t.ha[c]
            y[r] = t.east if c == 0 else t.north
    sigma = np.tile([params.prior_shift_m, params.prior_shift_m, np.radians(params.prior_shear_deg)], len(ids))
    prior = np.diag(1.0 / sigma)
    gauge: list[np.ndarray] = []
    for comp in _components(ids, {(t.a, t.b) for t in tiles}):
        for c in (0, 1):
            row = np.zeros(n)
            row[[3 * ix[s] + c for s in comp]] = 1.0
            gauge.append(row)
        ref = np.asarray(headings[comp[0]])
        fwd = [s for s in comp if float(np.dot(headings[s], ref)) >= 0]
        back = [s for s in comp if s not in fwd]
        row = np.zeros(n)
        groups = [fwd, back] if back else [fwd]
        for g in groups:
            row[[3 * ix[s] + 2 for s in g]] += 1.0 / len(g)
        gauge.append(row)
    G = np.array(gauge) * GAUGE_WEIGHT
    w0 = np.array([t.ncc**2 for t in tiles])
    keep = np.ones(len(tiles), dtype=bool)
    w = w0.copy()
    x = np.zeros(n)
    for it in range(IRLS_ROUNDS + 1):
        sw = np.repeat(np.sqrt(w * keep), 2)
        M = np.vstack([A * sw[:, None], prior, G])
        b = np.concatenate([y * sw, np.zeros(n + G.shape[0])])
        x = np.linalg.lstsq(M, b, rcond=None)[0]
        e = np.hypot(*(A @ x - y).reshape(-1, 2).T)
        if it == IRLS_ROUNDS - 1:
            keep = e <= params.outlier_m
        w = w0 * np.minimum(1.0, params.huber_m / np.maximum(e, 1e-9))
    return {s: x[3 * ix[s] : 3 * ix[s] + 3] for s in ids}, e, ~keep


def _median(v: list[float]) -> float | None:
    return float(np.median(v)) if v else None


def _p90(v: list[float]) -> float | None:
    return float(np.percentile(v, 90)) if v else None


def refine_strips(
    geoms: list[StripGeometry],
    read_band: BandReader,
    epsg: int,
    params: RefineParams,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> RefineResult:
    ids = [g.strip.index for g in geoms]
    result = RefineResult(corrections={s: (0.0, 0.0, 0.0) for s in ids})
    if not params.enabled:
        result.note = "未启用精配准"
        return result
    if len(geoms) < 2:
        result.note = "只有一条航带，无需精配准"
        return result
    planes: dict[int, np.ndarray] = {}

    def plane(g: StripGeometry) -> np.ndarray:
        if g.strip.index not in planes:
            planes[g.strip.index] = np.asarray(read_band(g.strip.line_start, g.strip.line_end), dtype=np.float32)
        return planes[g.strip.index]

    bounds = [g.bounds() for g in geoms]
    todo = [(i, j) for i in range(len(geoms)) for j in range(i + 1, len(geoms)) if _intersect(bounds[i], bounds[j])]
    headings = {g.strip.index: (float(np.median(g.hx)), float(np.median(g.hy))) for g in geoms}
    cur = list(geoms)
    total = {s: np.zeros(3) for s in ids}
    rounds = len(params.search_m)
    first: list[Tile] = []
    last: list[Tile] = []
    for rnd, radius in enumerate(params.search_m):
        tiles: list[Tile] = []
        for k, (i, j) in enumerate(todo):
            if cancelled and cancelled():
                raise Cancelled()
            if progress:
                what = "质检" if rnd == rounds - 1 else f"第 {rnd + 1} 轮"
                progress((rnd * len(todo) + k) / (rounds * len(todo)), f"精配准{what}：航带 {ids[i]} 与 {ids[j]}")
            others = [g for m, g in enumerate(cur) if m not in (i, j)]
            got = _measure_pair(cur[i], cur[j], others, plane, epsg, params, radius)
            if len(got) >= params.min_tiles_per_pair:
                tiles.extend(got)
        if rnd == 0:
            first = tiles
        if rnd == rounds - 1 or not tiles:
            last = tiles
            break
        inc, _, out = _solve(tiles, headings, params)
        result.outliers = int(out.sum())
        cur = [g.corrected(*inc[g.strip.index]) if g.strip.index in inc else g for g in cur]
        for s, v in inc.items():
            total[s] += v
    if not first:
        result.note = "重叠区没有足够的纹理块，未做精配准"
        return result
    result.before_m = _median([t.size for t in first])
    result.before_p90_m = _p90([t.size for t in first])
    result.after_m = _median([t.size for t in last])
    result.after_p90_m = _p90([t.size for t in last])
    result.pairs = _pair_stats(first, last)
    shear = max(abs(float(np.degrees(v[2]))) for v in total.values())
    shift = max(float(np.hypot(v[0], v[1])) for v in total.values())
    problem = ""
    if result.after_m is None or len(last) < len(first) // 2:
        problem = "修正后可用的纹理块太少"
    elif result.after_m >= result.before_m and result.after_m > ALREADY_ALIGNED_PX * params.gsd_m:
        problem = f"修正后错位没有变小（{result.before_m:.2f} → {result.after_m:.2f} m）"
    elif shear > params.max_shear_deg:
        problem = f"剪切 {shear:.1f}° 超出 {params.max_shear_deg:.0f}°"
    elif shift > params.max_shift_m:
        problem = f"平移 {shift:.2f} m 超出 {params.max_shift_m:.1f} m"
    if problem:
        result.note = f"精配准未采用：{problem}，保留整条平移的结果"
        log.warning("refine: %s", result.note)
        return result
    result.applied = True
    result.corrections = {s: (float(v[0]), float(v[1]), float(v[2])) for s, v in total.items()}
    deg = [float(np.degrees(v[2])) for v in total.values()]
    result.note = (
        f"精配准：{len({(t.a, t.b) for t in first})} 对航带 {len(first)} 块，"
        f"块内错位中位 {result.before_m:.2f} → {result.after_m:.2f} m"
        f"（p90 {result.before_p90_m:.2f} → {result.after_p90_m:.2f} m），"
        f"狭缝剪切 {min(deg):+.1f}° ～ {max(deg):+.1f}°"
    )
    log.info("refine: %s", result.note)
    return result


def _pair_stats(first: list[Tile], last: list[Tile]) -> list[PairStat]:
    out: dict[tuple[int, int], PairStat] = {}
    for tiles, before in ((first, True), (last, False)):
        groups: dict[tuple[int, int], list[Tile]] = {}
        for t in tiles:
            groups.setdefault((t.a, t.b), []).append(t)
        for key, ts in groups.items():
            p = out.setdefault(key, PairStat(*key))
            p.seam = p.seam or any(t.seam for t in ts)
            if before:
                p.tiles_before, p.before_m = len(ts), _median([t.size for t in ts])
            else:
                p.tiles_after, p.after_m = len(ts), _median([t.size for t in ts])
    return [out[k] for k in sorted(out)]
