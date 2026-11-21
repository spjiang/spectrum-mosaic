"""沿航向特征点配准。

整条平移和狭缝剪切只能表达「每条航带一个数」。GPS 沿航向漂移、或重复纹理让互相关
对到旁边一垄时，接缝上会留下随位置变化的台阶。这里在重叠区提 SIFT 特征点，
用描述子配对再做平移 RANSAC，沿航向每隔一段留一个平移，平滑后写回每行的地面位置。
第一条航带保持在 GPS 上，后面的航带逐条贴过去。只使用相邻航带的接缝：
隔一条的重叠里混着剪切，会把接缝上的平移带偏。特征太少，或错位又小、
沿航向也没有弯时，不改几何。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from pb_mosaic.geom.footprint import StripGeometry
from pb_mosaic.geom.grid import grid_from_bounds
from pb_mosaic.mosaic.align import MAX_PAIR_PIXELS, BandReader, _intersect, sample_strip
from pb_mosaic.mosaic.merge import Cancelled

log = logging.getLogger("pb_mosaic.tiepoints")

KNOT_M = 20.0
STATION_M = 14.0
# 一个测站的旁向窗口。太宽会把残余剪切混进平移
WINDOW_CROSS_M = 18.0
MIN_INLIERS = 8
INLIER_PX = 2.5
RATIO = 0.75
MAX_SHIFT_M = 6.0
# 错位中位低于这个值，并且沿航向的摆幅也很小，才认为现有配准已经够。
# 摆幅按单条接缝的东向、北向分开算：两端一正一负时，中位数接近 0，台阶还在。
MIN_MEDIAN_M = 0.35
MIN_SPAN_M = 0.6
MIN_BOW_M = 0.30
MIN_OBS = 6
# 接缝只看见相对平移。不把每个结点往 0 拉一点的话，往返航带会一起长出
# 一条接缝上互相抵消的大斜坡。
RIDGE = 0.15
# 二阶差分。太大时两端同时要改、中间不用改的形状会被拉成一条斜线。
SMOOTH = 0.15
# 比这还对不上的测站当粗差丢掉。真实的错位有 1–2 米，阈值太小会把要修的点一起丢掉。
OUTLIER_M = 1.5


@dataclass
class TieParams:
    enabled: bool = True
    gsd_m: float = 0.12


@dataclass
class Obs:
    a: int
    b: int
    ua: float
    ub: float
    east: float
    north: float
    inliers: int

    @property
    def size(self) -> float:
        return float(np.hypot(self.east, self.north))


@dataclass
class TieResult:
    applied: bool = False
    # 每条航带逐行的 (东, 北) 米
    lines: dict[int, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)
    observations: int = 0
    inliers: int = 0
    before_m: float | None = None
    after_m: float | None = None
    span_m: dict[int, float] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict:
        r = lambda v: None if v is None else round(float(v), 3)  # noqa: E731
        return {
            "applied": self.applied,
            "observations": self.observations,
            "inliers": self.inliers,
            "median_before_m": r(self.before_m),
            "median_after_m": r(self.after_m),
            "span_m": {str(k): round(v, 3) for k, v in self.span_m.items()},
            "note": self.note,
        }


def match_translation(
    ref: np.ndarray,
    tpl: np.ndarray,
    ref_ok: np.ndarray,
    tpl_ok: np.ndarray,
    *,
    max_shift_px: float,
) -> tuple[float, float, int] | None:
    """tpl 相对 ref 的平移 (dcol, drow) 及内点数。把 tpl 平移这么多像元后与 ref 重合。

    特征点对不上、或平移超出搜索范围时返回空。
    """
    import cv2

    a = _to_u8(ref, ref_ok)
    b = _to_u8(tpl, tpl_ok)
    if a is None or b is None:
        return None
    # 重叠有时只有几米宽。AKAZE / ORB 的描述子窗口比这条带还宽，会一个点都提不出来。
    sift = cv2.SIFT_create(nfeatures=800, contrastThreshold=0.02)
    mask_a = ref_ok.astype(np.uint8) * 255
    mask_b = tpl_ok.astype(np.uint8) * 255
    kpa, da = sift.detectAndCompute(a, mask_a)
    kpb, db = sift.detectAndCompute(b, mask_b)
    if da is None or db is None or len(kpa) < MIN_INLIERS or len(kpb) < MIN_INLIERS:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(da, db, k=2)
    disp = []
    for item in pairs:
        if len(item) < 2:
            continue
        m, n = item
        if m.distance >= RATIO * n.distance:
            continue
        pa, pb = kpa[m.queryIdx].pt, kpb[m.trainIdx].pt
        disp.append((pa[0] - pb[0], pa[1] - pb[1]))
    if len(disp) < MIN_INLIERS:
        return None
    shift = np.asarray(disp, dtype=np.float64)
    keep = _ransac_translation(shift, INLIER_PX)
    if int(keep.sum()) < MIN_INLIERS:
        return None
    dcol, drow = np.median(shift[keep], axis=0)
    if float(np.hypot(dcol, drow)) > max_shift_px:
        return None
    return float(dcol), float(drow), int(keep.sum())


def _to_u8(img: np.ndarray, ok: np.ndarray) -> np.ndarray | None:
    vals = img[ok]
    if vals.size < 64:
        return None
    lo, hi = np.percentile(vals, (2.0, 98.0))
    if hi - lo < 1e-3:
        return None
    out = np.zeros(img.shape, dtype=np.uint8)
    out[ok] = np.clip((img[ok] - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    return out


def _ransac_translation(shift: np.ndarray, tol: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    best: np.ndarray | None = None
    n = shift.shape[0]
    trials = 80 if n < 200 else 120
    for _ in range(trials):
        guess = shift[int(rng.integers(0, n))]
        err = np.hypot(shift[:, 0] - guess[0], shift[:, 1] - guess[1])
        keep = err <= tol
        if best is None or int(keep.sum()) > int(best.sum()):
            best = keep
    assert best is not None
    return best


def seam_bow_m(obs: list[Obs]) -> float:
    """同一条接缝上东向或北向平移的摆幅，取 90 分位与 10 分位之差里最大的。"""
    groups: dict[tuple[int, int], list[Obs]] = {}
    for o in obs:
        groups.setdefault((o.a, o.b), []).append(o)
    best = 0.0
    for rows in groups.values():
        if len(rows) < 6:
            continue
        for comp in (
            np.array([o.east for o in rows], dtype=np.float64),
            np.array([o.north for o in rows], dtype=np.float64),
        ):
            best = max(best, float(np.percentile(comp, 90) - np.percentile(comp, 10)))
    return best


def _along(geom: StripGeometry) -> np.ndarray:
    u = np.cumsum(geom.step)
    return u - u[0]


def _knots(length: float) -> np.ndarray:
    if length < KNOT_M:
        return np.array([0.0, max(length, 1.0)])
    count = int(length // KNOT_M) + 1
    return np.linspace(0.0, length, count)


def _lerp(u: float, knots: np.ndarray) -> tuple[int, float, int, float]:
    k = int(np.searchsorted(knots, u, side="right") - 1)
    k = min(max(k, 0), knots.size - 2)
    span = float(knots[k + 1] - knots[k]) or 1.0
    t = float(np.clip((u - knots[k]) / span, 0.0, 1.0))
    return k, 1.0 - t, k + 1, t


def _at_knot(values: np.ndarray, knots_u: np.ndarray, u: float) -> float:
    k0, w0, k1, w1 = _lerp(u, knots_u)
    return float(w0 * values[k0] + w1 * values[k1])


def solve_bends(
    obs: list[Obs], geoms: dict[int, StripGeometry]
) -> tuple[dict[int, tuple[np.ndarray, np.ndarray]], float, float]:
    """观测是「b 相对 a 还要平移 (east, north)」。

    第一条航带固定不动，其余航带按重叠顺序逐条贴上去。一起解会把改正
    分到本来已经对准的航带上，接缝没了，画面却被拽歪。
    返回每条航带的逐行平移、剩余错位的中位、以及 90 分位。
    """
    ids = sorted({i for o in obs for i in (o.a, o.b)})
    ref = ids[0]
    along = {i: _along(geoms[i]) for i in ids}
    knots = {i: _knots(float(along[i][-1])) for i in ids}
    east_k = {i: np.zeros(knots[i].size) for i in ids}
    north_k = {i: np.zeros(knots[i].size) for i in ids}

    def fit_strip(free: int, rows: list[Obs]) -> tuple[np.ndarray, np.ndarray]:
        kk = knots[free]
        m = kk.size

        def solve(use: list[Obs]) -> tuple[np.ndarray, np.ndarray]:
            smooth = max(m - 2, 0)
            rows_n = len(use) * 2 + smooth * 2 + m * 2
            # 东、北分开堆在一条未知数向量里：前 m 个东，后 m 个北
            A = np.zeros((rows_n, m * 2))
            y = np.zeros(rows_n)
            w = np.zeros(rows_n)
            r = 0
            for o in use:
                for comp, measured, table in (
                    (0, o.east, east_k),
                    (1, o.north, north_k),
                ):
                    k0, w0, k1, w1 = _lerp(o.ua if free == o.a else o.ub, kk)
                    sign = -1.0 if free == o.a else 1.0
                    other = o.b if free == o.a else o.a
                    other_u = o.ub if free == o.a else o.ua
                    fixed = _at_knot(table[other], knots[other], other_u)
                    # corr(b) - corr(a) = measured。正在求的那条放左边，已经固定的挪到右边。
                    A[r, comp * m + k0] = sign * w0
                    A[r, comp * m + k1] = sign * w1
                    y[r] = measured + fixed if free == o.b else measured - fixed
                    w[r] = min(o.inliers, 40) / 20.0
                    r += 1
            for comp in (0, 1):
                for k in range(1, m - 1):
                    A[r, comp * m + k - 1] = 1.0
                    A[r, comp * m + k] = -2.0
                    A[r, comp * m + k + 1] = 1.0
                    w[r] = SMOOTH
                    r += 1
                for k in range(m):
                    A[r, comp * m + k] = 1.0
                    w[r] = RIDGE
                    r += 1
            sw = np.sqrt(np.maximum(w[:r], 1e-8))
            x, *_ = np.linalg.lstsq(A[:r] * sw[:, None], y[:r] * sw, rcond=None)
            return x[:m], x[m:]

        def residual(ek: np.ndarray, nk: np.ndarray, o: Obs) -> float:
            def side(strip: int, u: float, comp: np.ndarray, own: np.ndarray | None) -> float:
                if strip == free:
                    return _at_knot(own, kk, u)
                return _at_knot(comp, knots[strip], u)

            pe = side(o.b, o.ub, east_k[o.b], ek if o.b == free else None) - side(
                o.a, o.ua, east_k[o.a], ek if o.a == free else None
            )
            pn = side(o.b, o.ub, north_k[o.b], nk if o.b == free else None) - side(
                o.a, o.ua, north_k[o.a], nk if o.a == free else None
            )
            return float(np.hypot(pe - o.east, pn - o.north))

        ek, nk = solve(rows)
        resid = [residual(ek, nk, o) for o in rows]
        kept = [o for o, e in zip(rows, resid) if e <= OUTLIER_M]
        if 3 <= len(kept) < len(rows):
            ek, nk = solve(kept)
            used = kept
        else:
            used = rows
        # 测站覆盖不到的两端保持最近一处的改正。拉回 0 会在航带尽头重新打开接缝。
        us = np.array([o.ub if free == o.b else o.ua for o in used], dtype=np.float64)
        lo, hi_u = float(us.min()), float(us.max())
        k_lo = int(np.argmin(np.abs(kk - lo)))
        k_hi = int(np.argmin(np.abs(kk - hi_u)))
        for k in range(m):
            if kk[k] < lo:
                ek[k], nk[k] = ek[k_lo], nk[k_lo]
            elif kk[k] > hi_u:
                ek[k], nk[k] = ek[k_hi], nk[k_hi]
        return ek, nk

    fixed = {ref}
    pending = [i for i in ids if i != ref]
    for _ in range(len(ids)):
        if not pending:
            break
        nxt: list[int] = []
        moved = False
        for i in pending:
            rows = [o for o in obs if (o.a == i and o.b in fixed) or (o.b == i and o.a in fixed)]
            if len(rows) < 3:
                nxt.append(i)
                continue
            east_k[i], north_k[i] = fit_strip(i, rows)
            fixed.add(i)
            moved = True
        pending = nxt
        if not moved:
            break

    def remain(o: Obs) -> float:
        pe = _at_knot(east_k[o.b], knots[o.b], o.ub) - _at_knot(east_k[o.a], knots[o.a], o.ua)
        pn = _at_knot(north_k[o.b], knots[o.b], o.ub) - _at_knot(north_k[o.a], knots[o.a], o.ua)
        return float(np.hypot(pe - o.east, pn - o.north))

    resid = [remain(o) for o in obs]
    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for i in ids:
        u = along[i]
        full_e = np.interp(u, knots[i], east_k[i])
        full_n = np.interp(u, knots[i], north_k[i])
        out[i] = (full_e, full_n)
    med = float(np.median(resid)) if resid else 0.0
    hi = float(np.percentile(resid, 90)) if resid else 0.0
    return out, med, hi


def bend_strips(
    geoms: list[StripGeometry],
    read_band: BandReader,
    epsg: int,
    params: TieParams,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> TieResult:
    ids = [g.strip.index for g in geoms]
    result = TieResult(lines={s: (np.zeros(g.n), np.zeros(g.n)) for s, g in zip(ids, geoms)})
    if not params.enabled:
        result.note = "未启用特征点配准"
        return result
    if len(geoms) < 2:
        result.note = "只有一条航带，不做特征点配准"
        return result
    try:
        import cv2  # noqa: F401
    except ImportError:
        result.note = "没有特征点库，跳过沿航向配准"
        return result

    by_id = {g.strip.index: g for g in geoms}
    planes: dict[int, np.ndarray] = {}

    def plane(g: StripGeometry) -> np.ndarray:
        if g.strip.index not in planes:
            planes[g.strip.index] = np.asarray(read_band(g.strip.line_start, g.strip.line_end), dtype=np.float32)
        return planes[g.strip.index]

    bounds = [g.bounds() for g in geoms]
    # 隔一条航带的重叠跨过中间那条，平移里混着剪切。接缝台阶只在相邻航带上。
    todo = [
        (i, j)
        for i in range(len(geoms))
        for j in range(i + 1, len(geoms))
        if abs(ids[i] - ids[j]) == 1 and _intersect(bounds[i], bounds[j])
    ]
    found: list[Obs] = []
    for k, (i, j) in enumerate(todo):
        if cancelled and cancelled():
            raise Cancelled()
        if progress:
            progress(k / max(len(todo), 1), f"特征点：航带 {ids[i]} 与 {ids[j]}")
        found.extend(_pair_obs(geoms[i], geoms[j], plane, epsg, params))
    result.observations = len(found)
    result.inliers = int(sum(o.inliers for o in found))
    if len(found) < MIN_OBS:
        result.note = f"特征点只有 {len(found)} 处可靠平移，未沿航向弯曲"
        return result
    sizes = [o.size for o in found]
    result.before_m = float(np.median(sizes))
    span = float(np.percentile(sizes, 90) - np.percentile(sizes, 10))
    bow = seam_bow_m(found)
    if result.before_m < MIN_MEDIAN_M and span < MIN_SPAN_M and bow < MIN_BOW_M:
        result.note = f"特征点看到的接缝错位中位 {result.before_m:.2f} m，不必再弯曲航带"
        return result
    solved, after, after_hi = solve_bends(found, by_id)
    result.after_m = after
    before_hi = float(np.percentile(sizes, 90))
    # 中位常常被已经对齐的那一段拉得很小。两端的弯绝对量不一定有 0.35 m，
    # 但较大的那几处应当明显降下来。
    if after_hi > 0.7 * before_hi or before_hi - after_hi < 0.10:
        result.note = f"特征点配准后较大错位仍有 {after_hi:.2f} m，未采用"
        return result
    if after > 0.5 and after > float(result.before_m) + 0.15:
        result.note = f"特征点配准把原来对齐的位置拉乱了（中位 {after:.2f} m），未采用"
        return result
    span_m: dict[int, float] = {}
    max_move = 0.0
    for s, (east, north) in solved.items():
        move = np.hypot(east, north)
        span_m[s] = float(move.max() - move.min()) if move.size else 0.0
        max_move = max(max_move, float(move.max()) if move.size else 0.0)
    if max_move > MAX_SHIFT_M:
        result.note = f"特征点平移最大 {max_move:.1f} m，超出 {MAX_SHIFT_M:.0f} m，未采用"
        return result
    if max_move < 0.15:
        result.note = "特征点平移可以忽略，未改航带"
        return result
    for s, arrays in solved.items():
        result.lines[s] = arrays
    result.span_m = span_m
    result.applied = True
    widest = max(span_m, key=span_m.get)
    result.note = (
        f"特征点 {result.observations} 处、{result.inliers} 个内点，"
        f"接缝错位中位 {result.before_m:.2f} m → {after:.2f} m，"
        f"航带 {widest} 沿航向改正 {span_m[widest]:.2f} m"
    )
    log.info(result.note)
    return result


def _pair_obs(
    ga: StripGeometry,
    gb: StripGeometry,
    plane: Callable[[StripGeometry], np.ndarray],
    epsg: int,
    params: TieParams,
) -> list[Obs]:
    box = _intersect(ga.bounds(), gb.bounds())
    if box is None:
        return []
    w, h = box[2] - box[0], box[3] - box[1]
    gsd = max(params.gsd_m, float(np.sqrt(max(w * h, 1.0) / MAX_PAIR_PIXELS)))
    grid = grid_from_bounds(epsg, box[0], box[1], box[2], box[3], gsd)
    ra = sample_strip(ga, grid, plane(ga), ga.strip.line_start)
    rb = sample_strip(gb, grid, plane(gb), gb.strip.line_start)
    both = ra.ok & rb.ok
    rows, cols = np.nonzero(both)
    if rows.size < 80:
        return []
    ua = _along(ga)
    ub = _along(gb)
    along = ua[np.clip(np.rint(ra.line[rows, cols]).astype(np.int64), 0, ga.n - 1)]
    step = STATION_M
    bin_id = np.floor(along / step).astype(np.int64)
    order = np.argsort(bin_id, kind="stable")
    bin_id = bin_id[order]
    starts = np.flatnonzero(np.r_[True, bin_id[1:] != bin_id[:-1]])
    ends = np.r_[starts[1:], bin_id.size]
    max_px = max(8, int(round(MAX_SHIFT_M / gsd)))
    cross_px = max(16, int(round(WINDOW_CROSS_M / gsd)))
    out: list[Obs] = []
    for s0, s1 in zip(starts, ends):
        if s1 - s0 < 16:
            continue
        sel = order[s0:s1]
        rr, cc = rows[sel], cols[sel]
        c_mid = int(np.median(cc))
        c0 = max(int(cc.min()), c_mid - cross_px // 2)
        c1 = min(int(cc.max()) + 1, c0 + cross_px)
        r0, r1 = int(rr.min()), int(rr.max()) + 1
        if r1 - r0 < 24 or c1 - c0 < 24:
            continue
        got = match_translation(
            ra.img[r0:r1, c0:c1],
            rb.img[r0:r1, c0:c1],
            ra.ok[r0:r1, c0:c1],
            rb.ok[r0:r1, c0:c1],
            max_shift_px=max_px,
        )
        if got is None:
            continue
        dcol, drow, nin = got
        la = float(np.nanmean(ra.line[rr, cc]))
        lb = float(np.nanmean(rb.line[rr, cc]))
        ia = int(np.clip(round(la), 0, ga.n - 1))
        ib = int(np.clip(round(lb), 0, gb.n - 1))
        out.append(
            Obs(
                a=ga.strip.index,
                b=gb.strip.index,
                ua=float(ua[ia]),
                ub=float(ub[ib]),
                east=dcol * gsd,
                north=-drow * gsd,
                inliers=nin,
            )
        )
    return out
