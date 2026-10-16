"""DSM 成果：粗差滤除、空洞填充、DTM 提取、GeoTIFF 输出。

输出规格对齐商业成品 `拼图结果/DSM.tif`：
float32、LZW 压缩、nodata = -3.4028235e+38、与正射同范围且 GSD 为正射的 2 倍。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ms_mosaic.dense import HeightField
from ms_mosaic.grid import Grid, inpaint_nearest, refine_coverage_mask

NODATA = np.float32(-3.4028234663852886e38)  # 与商业 DSM.tif 一致
# 稳健中值面的窗口。5 格（约 0.7 m）太小：成片锁错层时中值本身就是错的，
# 粗差检不出来。15 格（约 2 m）既大于树冠尺度，又小于地形起伏波长。
SPIKE_MEDIAN_CELLS = 15
SPIKE_TOLERANCE_M = 2.0
SMOOTH_MEDIAN_CELLS = 3
# 平面扫描的最优/次优代价相对差。实测 0.02 形同不设门限，与商业 DSM 的
# 中误差 25.6 m；0.10 时降到 4.7 m 且局部起伏与商业一致（见 scripts/probe_dense.py）。
MIN_CONFIDENCE = 0.10
DTM_OPENING_M = 12.0
# 空三点 p1/p99 给出地形带。余量由模版 terrain_margin_* 覆盖，下列是未填模版时的默认。
# MAX_20251017 示例方案写 30/50/80，对应本测区树高、AT 噪声与 161 m 起伏。
TERRAIN_REF_LO_PCT = 1.0
TERRAIN_REF_HI_PCT = 99.0
TERRAIN_MARGIN_LO_M = 30.0
TERRAIN_MARGIN_HI_M = 50.0
ROBUST_K = 3.0
ROBUST_MIN_HALF_SPAN_M = 80.0
ROBUST_FLYER_K = 8.0
# 航带边缘单视区：立体匹配经常解不出，但相片足迹还盖得到。商业正射把这块
# 铺上了；自研若只填封闭孔，缺口与测区外 nodata 连成一片就被 400k 上限跳过。
# 40 m ≈ 本测区缺失像元到有效 DSM 的 p99 距离，且小于右下角游离绿斑的间距。
MAX_FILL_GAP_M = 40.0


@dataclass
class Dsm:
    grid: Grid
    z: np.ndarray  # nan 表示无数据
    stats: dict

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.z)


def _pad_with_local(z: np.ndarray, *, cells: float = 8.0) -> np.ndarray:
    """把 nan 填成局部有效均值，作为邻域滤波的垫底值。

    不能用全图中值填：粗差剔除后有一两成格网散布全图为空，注入的全图中值
    与局部高程能差几十米，中值/中值平滑窗口一旦吃进这些值，结果就被拉偏
    （实测局部起伏 0.042 → 0.083 m，粗差率 10.8% → 18.4%）。
    这里用「高斯加权和 / 高斯加权有效数」做归一化卷积，等价于局部核回归。
    """
    from scipy.ndimage import gaussian_filter

    arr = np.asarray(z, np.float64)
    ok = np.isfinite(arr)
    if not ok.any():
        return np.zeros_like(arr)
    if ok.all():
        return arr
    num = gaussian_filter(np.where(ok, arr, 0.0), cells, mode="nearest")
    den = gaussian_filter(ok.astype(np.float64), cells, mode="nearest")
    local = np.where(den > 1e-6, num / np.maximum(den, 1e-12), np.nanmedian(arr[ok]))
    return np.where(ok, arr, local)


def remove_spikes(
    z: np.ndarray,
    *,
    median_cells: int = SPIKE_MEDIAN_CELLS,
    tolerance_m: float = SPIKE_TOLERANCE_M,
) -> np.ndarray:
    """去粗差：与稳健中值面偏离超过固定容差的格网判为噪声。

    容差不需要按坡度放宽。对称窗口下线性坡面的中值恰好等于窗口中心的值，
    所以再陡的**平面**坡都不会被误判；只有脊线/陡坎这类曲率大的地方中值才有
    偏差，量级是 曲率×窗口²，远小于容差。

    真正的关键是窗口要足够大。旧写法用 5 格窗口，成片锁错层时中值面本身就是
    错的，检不出粗差；于是又把容差放成 max(固定值, 4·MAD)，而植被区的 MAD
    有好几米，容差涨到十几米等于放弃检验 —— 实测内部仍留 3.7% 坑洞、4.4%
    尖刺，与商业 DSM 中误差 16 m。改成 15 格窗口 + 固定 2 m 容差后中误差
    降到 4.7 m，局部起伏与商业一致（scripts/probe_dense.py 的扫参记录）。
    """
    from scipy.ndimage import median_filter

    filled = np.where(np.isfinite(z), z, np.nan)
    if not np.isfinite(filled).any():
        return filled
    size = int(median_cells) | 1
    smooth = median_filter(_pad_with_local(filled, cells=size), size=size, mode="nearest")
    bad = np.isfinite(filled) & (np.abs(filled - smooth) > float(tolerance_m))
    return np.where(bad, np.nan, filled)


def median_smooth(
    z: np.ndarray,
    *,
    cells: int = SMOOTH_MEDIAN_CELLS,
) -> np.ndarray:
    """有效格网上的中值平滑（Kraus《Photogrammetry》DSM 去噪）。

    只改已有高程，不填空洞：空值用局部均值作滤波垫，写回时仍保持 nan。
    窗口 3 格（本测区约 0.3 m）压匹配噪声，建筑块不会被抹平。
    """
    from scipy.ndimage import median_filter

    filled = np.asarray(z, np.float64)
    ok = np.isfinite(filled)
    size = int(cells) | 1
    if int(ok.sum()) < 4 or size < 3:
        return filled
    smooth = median_filter(_pad_with_local(filled, cells=size), size=size, mode="nearest")
    return np.where(ok, smooth, np.nan)


OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _robust_sample(values: np.ndarray | None) -> np.ndarray:
    """去掉非有限值与 8·MAD 飞点，供地形带估计。空三点里出现过 −101 km。"""
    if values is None:
        return np.zeros(0, np.float64)
    z = np.asarray(values, np.float64).ravel()
    z = z[np.isfinite(z)]
    if z.size < 8:
        return z
    med = float(np.median(z))
    mad = 1.4826 * float(np.median(np.abs(z - med)))
    scale = max(mad, 1.0)
    return z[np.abs(z - med) <= ROBUST_FLYER_K * scale]


def plausible_z_limits(
    ref_z: np.ndarray | None = None,
    *,
    fallback_z: np.ndarray | None = None,
    lo_pct: float = TERRAIN_REF_LO_PCT,
    hi_pct: float = TERRAIN_REF_HI_PCT,
    margin_lo_m: float = TERRAIN_MARGIN_LO_M,
    margin_hi_m: float = TERRAIN_MARGIN_HI_M,
    robust_k: float = ROBUST_K,
    min_half_span_m: float = ROBUST_MIN_HALF_SPAN_M,
) -> tuple[float, float] | None:
    """合理高程带。优先空三/GPS 分位数，没有参考时才用格网中值 ± k·MAD。

    不能对 DSM 自身做 0.2–99.8 分位：成片匹配失败会把 p0.2 拉到几百米，
    再加 0.25·span 余量后 −9682 m 仍算「有效」，GIS 一拉就把地形拉成白片，
    真正射在坑里会取错相片。商业 2.5D DSM 的有效值落在稀疏点包络内。
    """
    sample = _robust_sample(ref_z)
    use_ref = sample.size >= 32
    if not use_ref:
        sample = _robust_sample(fallback_z)
    if sample.size < 8:
        return None
    if use_ref:
        lo = float(np.percentile(sample, lo_pct)) - float(margin_lo_m)
        hi = float(np.percentile(sample, hi_pct)) + float(margin_hi_m)
    else:
        med = float(np.median(sample))
        mad = 1.4826 * float(np.median(np.abs(sample - med)))
        half = max(float(min_half_span_m), float(robust_k) * max(mad, 1.0))
        lo, hi = med - half, med + half
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    return lo, hi


def terrain_clip_kwargs(
    *,
    margin_lo_m: float | None = None,
    margin_hi_m: float | None = None,
    min_half_span_m: float | None = None,
) -> dict:
    """把模版里的地形带余量收成 clip 函数的关键字。None 表示用代码默认。"""
    kw: dict = {}
    if margin_lo_m is not None:
        kw["margin_lo_m"] = float(margin_lo_m)
    if margin_hi_m is not None:
        kw["margin_hi_m"] = float(margin_hi_m)
    if min_half_span_m is not None:
        kw["min_half_span_m"] = float(min_half_span_m)
    return kw


def clip_z_to_plausible(
    z: np.ndarray,
    *,
    ref_z: np.ndarray | None = None,
    **kwargs,
) -> np.ndarray:
    """把格网裁到 plausible_z_limits；带宽外写成 nan，留给后面补洞。"""
    limits = plausible_z_limits(ref_z, fallback_z=z, **kwargs)
    if limits is None:
        return z
    lo, hi = limits
    out = np.array(z, dtype=np.float64, copy=True)
    bad = np.isfinite(out) & ((out < lo) | (out > hi))
    out[bad] = np.nan
    return out


def _clip_z_outliers(
    z: np.ndarray,
    *,
    ref_z: np.ndarray | None = None,
    lo_pct: float = TERRAIN_REF_LO_PCT,
    hi_pct: float = TERRAIN_REF_HI_PCT,
    **kwargs,
) -> np.ndarray:
    """兼容旧名：地形带裁飞点，不再用被污染的 DSM 分位数。"""
    return clip_z_to_plausible(z, ref_z=ref_z, lo_pct=lo_pct, hi_pct=hi_pct, **kwargs)


def fill_holes(
    z: np.ndarray,
    *,
    max_component_cells: int = 400_000,
    domain: np.ndarray | None = None,
    trend: np.ndarray | None = None,
) -> np.ndarray:
    """空洞填充：在空洞内求解拉普拉斯方程，边界取周围的有效高程。

    解 ∇²z = 0 得到的是调和插值面，它与空洞边界的高程和坡度自然衔接。

    但调和面还有一条性质在这里是有害的：最大值原理保证洞内不会出现新的极值。
    山脊顶部往往视角重数低、匹配解不出，整个山顶落在一个空洞里，于是必然被压
    到洞沿的高度 —— 实测最高的一成地面因此系统性偏低 23 m，全幅中误差 16.7 m，
    QGIS 自动拉伸也被这一圈撑宽、内部看起来发白。

    trend 给出趋势面时改解残差 z − trend 的调和方程，回填再加回 trend（带漂移项
    的插值）。留着这个入口是因为它在「山顶整片解不出」时理论上能兜住，但实测
    用空三先验面当趋势反而更差：山顶窗口中误差 5.20 m → 119.3 m，先验面里的
    空三飞点被核回归摊开了。所以主路径不传 trend；真正压住山顶偏低的是把
    ref_z 交给 plausible_z_limits（见 build_dsm），而不是换插值方法。

    一个自然的想法是「反复用有效邻域均值向内扩散」，但那样每个格网只取一次
    已定值邻域的均值就冻结，会把坡度逐层抹平：0.5 m/格 的坡面上误差可达 1 m。
    所以这里按连通域组装稀疏线性方程直接解，线性坡面能精确复原。

    domain 给出「应当有 DSM 的范围」（相片足迹 ∩ 距主体不太远）。没有 domain
    时，与测区外 nodata 连成一片的缺口会被当成超大空洞跳过；给了 domain 后
    只在域内补，大缺口改用最近邻（EDT），避免百万格 Laplace 撑爆内存。
    """
    from scipy.ndimage import find_objects, label
    from scipy.sparse import csr_matrix
    from scipy.sparse.linalg import spsolve

    base = None
    if trend is not None:
        base = np.asarray(trend, np.float64)
        if base.shape != np.shape(z) or not np.isfinite(base).any():
            base = None
        elif not np.isfinite(base).all():
            # 趋势面有缺口时先补成局部均值，否则残差会连带变成 nan
            base = _pad_with_local(base)
    if base is not None:
        filled = fill_holes(
            np.asarray(z, np.float64) - base,
            max_component_cells=max_component_cells,
            domain=domain,
        )
        return filled + base

    out = np.array(z, dtype=np.float64, copy=True)
    hole = ~np.isfinite(out)
    if domain is not None:
        hole = hole & np.asarray(domain, bool)
    if not hole.any():
        return out
    if hole.all():
        return np.full_like(out, np.nan)

    height, width = out.shape
    # 必须按包围盒解：对每个连通域做 `components == id` 会扫整幅 DSM。
    # 全测区约 4000 万格、上万个小洞时，旧写法会卡死在补洞阶段。
    components, n_comp = label(hole)
    if n_comp == 0:
        return out
    slices = find_objects(components)
    for comp, slc in enumerate(slices, start=1):
        if slc is None:
            continue
        r0 = max(0, slc[0].start - 1)
        r1 = min(height, slc[0].stop + 1)
        c0 = max(0, slc[1].start - 1)
        c1 = min(width, slc[1].stop + 1)
        sub = components[r0:r1, c0:c1]
        mask = sub == comp
        n = int(mask.sum())
        if n == 0 or n > max_component_cells:
            # 超大空洞：无 domain 时是测区外，留 nodata；有 domain 时交给后面最近邻
            continue
        h, w = sub.shape
        idx = np.full((h, w), -1, np.int64)
        idx[mask] = np.arange(n)
        rows_i, cols_i = np.nonzero(mask)
        sub_z = out[r0:r1, c0:c1]

        diag = np.zeros(n)
        rhs = np.zeros(n)
        a_rows: list[np.ndarray] = []
        a_cols: list[np.ndarray] = []
        for dr, dc in OFFSETS:
            nr, nc = rows_i + dr, cols_i + dc
            inside = (nr >= 0) & (nr < h) & (nc >= 0) & (nc < w)
            sel = np.nonzero(inside)[0]
            if sel.size == 0:
                continue
            nb = idx[nr[sel], nc[sel]]
            is_hole = nb >= 0
            z_nb = sub_z[nr[sel], nc[sel]]
            is_known = (~is_hole) & np.isfinite(z_nb)
            use = is_hole | is_known
            diag[sel[use]] += 1
            a_rows.append(sel[is_hole])
            a_cols.append(nb[is_hole])
            np.add.at(rhs, sel[is_known], z_nb[is_known])

        if not np.any(diag > 0):
            continue
        r = np.concatenate([np.arange(n), *a_rows])
        c = np.concatenate([np.arange(n), *a_cols])
        v = np.concatenate([np.maximum(diag, 1.0), *[-np.ones(x.size) for x in a_rows]])
        # 完全被空洞包围的孤岛（rhs 全零且无已知邻居）无解，跳过留 nodata
        if not np.any(rhs != 0.0):
            continue
        sol = spsolve(csr_matrix((v, (r, c)), shape=(n, n)), rhs)
        out[rows_i + r0, cols_i + c0] = sol

    if domain is not None:
        take = np.asarray(domain, bool) | np.isfinite(out)
        still = take & ~np.isfinite(out)
        if still.any() and np.isfinite(out).any():
            # 先在粗格网上把大缺口解成调和面，剩下的零星格再用最近邻兜底
            out = _fill_large_gaps_coarse(
                out, take, max_component_cells=max_component_cells
            )
            if (take & ~np.isfinite(out)).any():
                out = inpaint_nearest(out, np.isfinite(out), take)
    return out


def flatten_edge_z(
    z: np.ndarray,
    gsd: float,
    *,
    win_m: float = 40.0,
    band_m: float = 80.0,
) -> np.ndarray:
    """贴边一圈换成粗格网均值面，只供真正射采样，不改交付 DSM。

    测区边缘密集匹配经常锁到地面（本测区西缘比商业冠层低 26 m），真正射再按
    这张起皱的地面去反投树冠相片，相邻格从相片上挤在一起的像素取值，肉眼就是
    油彩波纹。窗口实验：原 DSM 重投仍是波纹；改用窗口常值中位面后树冠纹理恢复，
    与商业边缘观感一致。

    错误 DSM 上的真正射比平面正射更差（Amhar et al. 1998；Kraus《Photogrammetry》
    正射与真正射的取舍）。贴边退回局部水平面纠正：先按 win_m 块平均（等价低通），
    再只写回距 nodata ≤ band_m 的格子。内部真正射不动。
    """
    from scipy.ndimage import distance_transform_edt, zoom

    arr = np.asarray(z, np.float64)
    ok = np.isfinite(arr)
    if not ok.any():
        return arr
    f = max(2, int(round(float(win_m) / max(float(gsd), 1e-9))))
    coarse = _block_reduce_nanmean(arr, f)
    fill = float(np.nanmedian(arr[ok]))
    plane = zoom(np.where(np.isfinite(coarse), coarse, fill), f, order=1)[: arr.shape[0], : arr.shape[1]]
    dist = distance_transform_edt(ok) * float(gsd)
    return np.where(ok & (dist <= float(band_m)), plane, arr)


def _block_reduce_nanmean(a: np.ndarray, f: int) -> np.ndarray:
    """按 f×f 块取有效值均值降采样，全空的块给 nan。"""
    h, w = a.shape
    ph, pw = (-h) % f, (-w) % f
    if ph or pw:
        a = np.pad(a, ((0, ph), (0, pw)), constant_values=np.nan)
    ok = np.isfinite(a)
    num = np.where(ok, a, 0.0).reshape(a.shape[0] // f, f, a.shape[1] // f, f).sum(axis=(1, 3))
    cnt = ok.reshape(a.shape[0] // f, f, a.shape[1] // f, f).sum(axis=(1, 3))
    return np.where(cnt > 0, num / np.maximum(cnt, 1), np.nan)


def _fill_large_gaps_coarse(
    z: np.ndarray, take: np.ndarray, *, max_component_cells: int
) -> np.ndarray:
    """大缺口在粗格网上解调和方程，再双线性放回，避免最近邻铺出平台。

    原先大缺口（超过 max_component_cells 的连通域）直接跳过 Laplace，最后由
    最近邻兜底。最近邻把整片缺口铺成离它最近那个有效格的**常值**，在往测区
    外沿外推数十米时就是一块平台。实测交付 DSM 里这类「3×3 完全等值」的格网
    占 11.1%，其中 46 个团块超过 1 万格、最大 178360 格；它们的高程偏差
    −36.1 m、中误差 40.8 m、|d| 的 p90 达 93.9 m，而其余真匹配格网只有
    −1.3 m / 10.5 m / 7.9 m —— 全幅 16.7 m 的中误差几乎全由这一项贡献，
    DSM 最低值也被它拉到 1626 m（商业 1651 m），显示动态范围被撑宽 35%，
    内部地形看起来就发白。

    降采样到缺口规模能解为止再求调和面，是多重网格求 Poisson/Laplace 的常规
    做法（Briggs et al., *A Multigrid Tutorial*；Burt & Adelson 金字塔同源）。
    粗层解出的面仍与缺口边界的高程和坡度衔接，放回细格网后不会出现平台。
    """
    missing = take & ~np.isfinite(z)
    n_missing = int(missing.sum())
    if n_missing == 0:
        return z
    # 粗层的「应有范围」按块取并集，会比 n_missing/f² 略大，留 4 倍余量兜住
    budget = max(int(max_component_cells) // 4, 1024)
    f = 1
    while n_missing // (f * f) > budget:
        f *= 2
    if f == 1:
        return z
    from scipy.ndimage import zoom

    coarse_z = _block_reduce_nanmean(np.asarray(z, np.float64), f)
    coarse_take = _block_reduce_nanmean(take.astype(np.float64), f) > 0.0
    solved = fill_holes(coarse_z, max_component_cells=max_component_cells, domain=coarse_take)
    if not np.isfinite(solved).any():
        return z
    up = zoom(solved, f, order=1, mode="nearest")[: z.shape[0], : z.shape[1]]
    out = np.array(z, np.float64, copy=True)
    # 粗层若仍有解不出的格，保持 nan 交给后面的最近邻兜底，不要塞全图中值
    paste = missing & np.isfinite(up)
    out[paste] = up[paste]
    return out


def _fill_domain(
    valid: np.ndarray,
    coverage: np.ndarray,
    gsd: float,
    max_fill_gap_m: float | None,
) -> np.ndarray:
    """应有 DSM 的范围：足迹/商业覆盖。max_fill_gap_m 为 None 时填满覆盖域。"""
    from scipy.ndimage import distance_transform_edt

    cov = np.asarray(coverage, bool)
    valid = np.asarray(valid, bool)
    if max_fill_gap_m is None:
        return valid | cov
    gap_cells = max(1, int(round(float(max_fill_gap_m) / max(float(gsd), 1e-9))))
    dist = distance_transform_edt(~valid)
    return valid | (cov & (dist <= gap_cells))


def build_dsm(
    field: HeightField,
    *,
    min_confidence: float = MIN_CONFIDENCE,
    tolerance_m: float = SPIKE_TOLERANCE_M,
    fill: bool = True,
    coverage: np.ndarray | None = None,
    max_fill_gap_m: float | None = MAX_FILL_GAP_M,
    clip_to_coverage: bool = False,
    ref_z: np.ndarray | None = None,
    margin_lo_m: float | None = None,
    margin_hi_m: float | None = None,
    min_half_span_m: float | None = None,
    trend: np.ndarray | None = None,
) -> Dsm:
    """由密集匹配的高程场生成 DSM。trend 为补洞用的趋势面，见 fill_holes。"""
    z = field.z.copy()
    low_conf = np.isfinite(field.confidence) & (field.confidence < min_confidence)
    z = np.where(low_conf, np.nan, z)
    raw_valid = int(np.isfinite(z).sum())

    clip_kw = terrain_clip_kwargs(
        margin_lo_m=margin_lo_m, margin_hi_m=margin_hi_m, min_half_span_m=min_half_span_m
    )
    # 地形带必须先于邻域去尖：成片低坑的局部中值也是错的，MAD 去尖放不过。
    limits = plausible_z_limits(ref_z, fallback_z=z, **clip_kw)
    z = clip_z_to_plausible(z, ref_z=ref_z, **clip_kw)
    after_range = int(np.isfinite(z).sum())
    z = remove_spikes(z, tolerance_m=tolerance_m)
    after_spikes = int(np.isfinite(z).sum())
    main = refine_coverage_mask(np.isfinite(z), max_hole_cells=0, merge_gap_cells=4)
    z = np.where(main, z, np.nan)
    if fill:
        domain = None
        if coverage is not None:
            domain = _fill_domain(main, coverage, field.grid.gsd, max_fill_gap_m)
        z = fill_holes(z, domain=domain, trend=trend)
        if clip_to_coverage and coverage is not None:
            z = np.where(np.asarray(coverage, bool), z, np.nan)
    # 中值平滑放在补洞之后：补洞前做的话，剔除留下的散点空洞会让每个窗口都
    # 吃进垫底值，等于在真实高程上叠一层插值噪声。
    z = median_smooth(z)

    valid = np.isfinite(z)
    stats = {
        "gsd": field.grid.gsd,
        "width": field.grid.width,
        "height": field.grid.height,
        "n_valid": int(valid.sum()),
        "fill_ratio": float(valid.mean()),
        "dropped_low_confidence": int(np.isfinite(field.z).sum() - raw_valid),
        "dropped_range": int(raw_valid - after_range),
        "dropped_spikes": int(after_range - after_spikes),
        "z_clip_lo": None if limits is None else float(limits[0]),
        "z_clip_hi": None if limits is None else float(limits[1]),
        "z_min": float(np.nanmin(z)) if valid.any() else float("nan"),
        "z_median": float(np.nanmedian(z)) if valid.any() else float("nan"),
        "z_max": float(np.nanmax(z)) if valid.any() else float("nan"),
        "mean_point_spacing_m": field.grid.gsd,
    }
    return Dsm(field.grid, z.astype(np.float32), stats)


def complete_dsm_coverage(
    z: np.ndarray,
    grid: Grid,
    coverage: np.ndarray,
    *,
    max_fill_gap_m: float | None = MAX_FILL_GAP_M,
    clip_to_coverage: bool = False,
    ref_z: np.ndarray | None = None,
    margin_lo_m: float | None = None,
    margin_hi_m: float | None = None,
    min_half_span_m: float | None = None,
    trend: np.ndarray | None = None,
) -> np.ndarray:
    """已有 DSM 沿覆盖域补缺口。先裁地形带再补，避免坑里的飞点被最近邻铺开。"""
    clip_kw = terrain_clip_kwargs(
        margin_lo_m=margin_lo_m, margin_hi_m=margin_hi_m, min_half_span_m=min_half_span_m
    )
    z = clip_z_to_plausible(np.asarray(z, np.float64), ref_z=ref_z, **clip_kw)
    z = remove_spikes(z)
    main = np.isfinite(z)
    domain = _fill_domain(main, coverage, grid.gsd, max_fill_gap_m)
    out = fill_holes(z, domain=domain, trend=trend)
    if clip_to_coverage:
        out = np.where(np.asarray(coverage, bool), out, np.nan)
    return out


def to_dtm(dsm: Dsm, *, opening_m: float = DTM_OPENING_M) -> Dsm:
    """由 DSM morphological opening 得到 DTM（地面模型）。

    灰度形态学开运算按窗口取局部最低面，能压掉建筑与树冠；窗口需大于
    要滤除的地物平面尺寸，小于地形本身的起伏波长。
    """
    from scipy.ndimage import grey_opening, uniform_filter

    size = max(3, int(round(opening_m / dsm.grid.gsd)) | 1)
    filled = fill_holes(dsm.z.astype(np.float64))
    proxy = np.where(np.isfinite(filled), filled, np.nanmax(filled))
    ground = grey_opening(proxy, size=size, mode="nearest")
    ground = uniform_filter(ground, size // 2 | 1, mode="nearest")
    ground = np.where(np.isfinite(dsm.z), ground, np.nan)
    stats = dict(dsm.stats)
    stats["opening_m"] = opening_m
    stats["z_median"] = float(np.nanmedian(ground))
    return Dsm(dsm.grid, ground.astype(np.float32), stats)


def resample_height(
    z: np.ndarray,
    src: Grid,
    dst: Grid,
    window: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """把 src 格网上的高程双线性重采样到 dst（或 dst 的一个窗口）。

    正射格网是 DSM 的 1:2 细化，真正射必须在正射分辨率上取高程。无效值
    （nan）不能当成 0 参与插值，否则空洞边缘会被拉出假斜坡。
    """
    from scipy.ndimage import map_coordinates

    xs, ys = dst.cell_centers(window)
    inv = ~src.transform
    cols, rows = inv * (xs, ys)
    # rasterio 的像素坐标把整数落在像元左上角，map_coordinates 把 0 当成第 0 个
    # 像元中心，差半个像元。不减 0.5 会把线性坡面整体平移 0.5 格。
    cols = np.asarray(cols, np.float64) - 0.5
    rows = np.asarray(rows, np.float64) - 0.5
    valid = np.isfinite(z).astype(np.float64)
    proxy = np.where(np.isfinite(z), z, 0.0).astype(np.float64)
    sampled = map_coordinates(proxy, [rows, cols], order=1, mode="nearest", prefilter=False)
    weight = map_coordinates(valid, [rows, cols], order=1, mode="nearest", prefilter=False)
    # 空洞填了 0，必须除掉权重。否则贴着 nodata、权重刚过 0.5 的格子
    # 高程会被拉向 0（本测区北缘从 1790 m 掉到 899 m），真正射就拉出竖条。
    corrected = sampled / np.maximum(weight, 1e-6)
    out = np.where(weight > 0.5, corrected, np.nan)
    return out.astype(np.float32)


def write_geotiff(dsm: Dsm, path: Path) -> Path:
    """写出与商业 DSM.tif 同规格的单波段 float32 GeoTIFF。"""
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.where(np.isfinite(dsm.z), dsm.z, NODATA).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=dsm.grid.width,
        height=dsm.grid.height,
        count=1,
        dtype="float32",
        crs=dsm.grid.crs,
        transform=dsm.grid.transform,
        nodata=float(NODATA),
        compress="lzw",
        tiled=False,
        blockxsize=dsm.grid.width,
        blockysize=1,
        interleave="band",
        BIGTIFF="IF_SAFER",
    ) as dst:
        dst.write(data, 1)
        dst.update_tags(AREA_OR_POINT="Area")
    return path
