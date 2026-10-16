"""与商业成品的量化比对。

用途是把「看起来差不多」变成可核对的数字：范围套合、GSD、DSM 高程差
（中误差 / 相关系数）、正射的几何与辐射一致性。所有比对都把双方重采样
到同一格网上再算，避免分辨率或原点差异混进误差里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class RasterRef:
    """被比对的栅格。只保留比对需要的元信息，避免整幅读入内存。"""

    path: Path
    width: int
    height: int
    count: int
    dtype: str
    crs: str
    gsd: float
    bounds: tuple[float, float, float, float]
    nodata: float | None

    @classmethod
    def open(cls, path: Path) -> RasterRef:
        import rasterio

        with rasterio.open(path) as src:
            return cls(
                path=Path(path),
                width=src.width,
                height=src.height,
                count=src.count,
                dtype=src.dtypes[0],
                crs=str(src.crs),
                gsd=float(src.transform.a),
                bounds=tuple(float(v) for v in src.bounds),
                nodata=None if src.nodata is None else float(src.nodata),
            )


@dataclass
class ComparisonItem:
    name: str
    ours: float | str | None
    theirs: float | str | None
    delta: float | str | None = None
    tolerance: float | None = None
    passed: bool | None = None
    note: str = ""


@dataclass
class ComparisonReport:
    items: list[ComparisonItem] = field(default_factory=list)

    def add(
        self,
        name: str,
        ours,
        theirs,
        *,
        delta=None,
        tolerance: float | None = None,
        note: str = "",
    ) -> None:
        passed = None
        if delta is not None and tolerance is not None:
            try:
                passed = abs(float(delta)) <= tolerance
            except (TypeError, ValueError):
                passed = None
        self.items.append(
            ComparisonItem(name, ours, theirs, delta, tolerance, passed, note)
        )

    @property
    def ok(self) -> bool:
        return all(i.passed for i in self.items if i.passed is not None)

    def to_text(self) -> str:
        """Markdown 表格。DSM、正射、多光谱分节，方便直接预览。"""

        def cell(v) -> str:
            if v is None or v == "":
                return ""
            if isinstance(v, float):
                text = f"{v:.6g}"
            else:
                text = str(v)
            return text.replace("|", "\\|")

        groups: dict[str, list[ComparisonItem]] = {"DSM": [], "正射": [], "多光谱": []}
        for item in self.items:
            if item.name.startswith("MS"):
                groups["多光谱"].append(item)
            elif item.name.startswith("DSM"):
                groups["DSM"].append(item)
            else:
                groups["正射"].append(item)

        lines = ["# 比对报告", ""]
        header = "| 项目 | 自研 | 商业 | 差值 | 判定 | 说明 |"
        rule = "| --- | ---: | ---: | ---: | --- | --- |"
        for title, items in groups.items():
            if not items:
                continue
            lines.append(f"## {title}")
            lines.append("")
            lines.append(header)
            lines.append(rule)
            for item in items:
                if item.passed is None:
                    mark = ""
                else:
                    mark = "通过" if item.passed else "未达标"
                lines.append(
                    f"| {cell(item.name)} | {cell(item.ours)} | {cell(item.theirs)} | "
                    f"{cell(item.delta)} | {mark} | {cell(item.note)} |"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _band_as_float(src, band: int = 1) -> np.ndarray:
    """读出一个波段为 float32，nodata / alpha=0 写成 nan。uint8 不能 filled(nan)。"""
    arr = src.read(band).astype(np.float32)
    nodata = src.nodata
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)
    if src.count >= 4:
        arr = np.where(src.read(src.count) > 0, arr, np.nan)
    return arr


def _read_on_grid(path: Path, ref_path: Path, band: int = 1) -> np.ndarray:
    """把 path 重采样到 ref_path 的格网上读出，nodata 转 nan。"""
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(ref_path) as ref:
        dst = np.full((ref.height, ref.width), np.nan, np.float32)
        with rasterio.open(path) as src:
            arr = _band_as_float(src, band)
            reproject(
                source=arr,
                destination=dst,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=ref.transform,
                dst_crs=ref.crs,
                src_nodata=np.nan,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
    return dst


def _corr_shortfall(corr: float, floor: float) -> float:
    """低于相关系数门槛的缺口。达到门槛时为 0，供 abs(差值)≤0 判通过。"""
    if not np.isfinite(corr):
        return float(floor)
    return float(max(0.0, floor - corr))


def compare_dsm(ours: Path, theirs: Path, *, report: ComparisonReport | None = None):
    """比对 DSM：范围、GSD、覆盖率，以及重叠区的高程差统计。"""
    rep = report or ComparisonReport()
    a = RasterRef.open(ours)
    b = RasterRef.open(theirs)

    rep.add("DSM 坐标系", a.crs, b.crs, delta=0.0 if a.crs == b.crs else 1.0, tolerance=0.0)
    rep.add("DSM GSD (m)", a.gsd, b.gsd, delta=a.gsd - b.gsd, tolerance=1e-9)
    for i, label in enumerate(("左", "下", "右", "上")):
        rep.add(
            f"DSM 范围{label} (m)",
            a.bounds[i],
            b.bounds[i],
            delta=a.bounds[i] - b.bounds[i],
            tolerance=1e-4,
        )

    # 统一到商业格网上比高程
    ours_on_b = _read_on_grid(ours, theirs)
    import rasterio

    with rasterio.open(theirs) as src:
        theirs_arr = src.read(1, masked=True).filled(np.nan).astype(np.float32)

    both = np.isfinite(ours_on_b) & np.isfinite(theirs_arr)
    rep.add(
        "DSM 重叠区覆盖率",
        float(np.isfinite(ours_on_b).mean()),
        float(np.isfinite(theirs_arr).mean()),
        note="自研 / 商业 各自的有效格网占比",
    )
    if both.sum() < 100:
        rep.add("DSM 高程比对", None, None, note="重叠格网不足，无法比对")
        return rep

    diff = ours_on_b[both] - theirs_arr[both]
    bias = float(np.mean(diff))
    rep.add("DSM 高程偏差均值 (m)", bias, 0.0, delta=bias, tolerance=3.0)
    rep.add(
        "DSM 高程中误差 (m)",
        float(np.sqrt(np.mean(diff**2))),
        0.0,
        delta=float(np.sqrt(np.mean((diff - bias) ** 2))),
        tolerance=3.0,
        note="差值为去除系统偏差后的中误差",
    )
    rep.add("DSM 高程差 P90 (m)", float(np.percentile(np.abs(diff - bias), 90)), None)
    corr = float(np.corrcoef(ours_on_b[both], theirs_arr[both])[0, 1])
    rep.add(
        "DSM 相关系数",
        corr,
        1.0,
        delta=_corr_shortfall(corr, 0.95),
        tolerance=0.0,
        note="要求 r≥0.95，差值为低于门槛的缺口",
    )

    # 无控制点时，整体高差与倾斜只反映两套 GNSS 定权策略的差别，不是面形误差。
    # 拟合掉「常数 + 东向/北向倾斜」这 3 个自由度后的残差，才是真正的面形一致性。
    plane = fit_plane_residual(ours_on_b, theirs_arr, both, b.gsd)
    rep.add("DSM 基准高差 (m)", plane["bias"], 0.0, note="拟合平面的常数项")
    rep.add("DSM 基准东向倾斜 (mm/m)", plane["slope_e_mm_per_m"], 0.0)
    rep.add("DSM 基准北向倾斜 (mm/m)", plane["slope_n_mm_per_m"], 0.0)
    rep.add(
        "DSM 去基准后中误差 (m)",
        plane["rmse"],
        0.0,
        delta=plane["rmse"],
        tolerance=1.0,
        note="扣除常数+倾斜后的面形中误差",
    )
    rep.add("DSM 去基准后 P90 (m)", plane["p90"], None)
    return rep


def fit_plane_residual(
    ours: np.ndarray, theirs: np.ndarray, mask: np.ndarray, gsd: float
) -> dict:
    """对两幅高程面的差值拟合一个平面，返回平面参数与去除平面后的残差统计。

    倾斜量以 mm/m 给出：条带块在缺少高程控制时，最容易出现的就是沿航线的
    整体倾斜（俗称「弯曲」），量级 10 mm/m 在 400 m 跨度上就是 4 m 的高差。
    """
    rows, cols = np.nonzero(mask)
    d = (ours[mask] - theirs[mask]).astype(np.float64)
    # 自变量换成米，斜率才有 m/m 的量纲
    x = (cols - cols.mean()) * gsd
    y = (rows - rows.mean()) * gsd
    a = np.stack([np.ones_like(x), x, y], axis=1)
    coef, *_ = np.linalg.lstsq(a, d, rcond=None)
    # einsum 而非 @：Accelerate 的 BLAS 在大矩阵乘时会刷虚假的浮点异常告警
    resid = d - np.einsum("ij,j->i", a, coef, optimize=True)
    return {
        "bias": float(coef[0]),
        # 行号朝南增大，北向坡度取反号
        "slope_e_mm_per_m": float(coef[1] * 1000.0),
        "slope_n_mm_per_m": float(-coef[2] * 1000.0),
        "rmse": float(np.sqrt(np.mean(resid**2))),
        "p90": float(np.percentile(np.abs(resid), 90)),
        "n": int(mask.sum()),
    }


def compare_ortho(ours: Path, theirs: Path, *, report: ComparisonReport | None = None):
    """比对正射：规格必须逐项对齐，重叠区再比辐射与覆盖。"""
    import rasterio

    rep = report or ComparisonReport()
    a = RasterRef.open(ours)
    b = RasterRef.open(theirs)
    rep.add("正射坐标系", a.crs, b.crs, delta=0.0 if a.crs == b.crs else 1.0, tolerance=0.0)
    rep.add("正射 GSD (m)", a.gsd, b.gsd, delta=a.gsd - b.gsd, tolerance=1e-9)
    rep.add("正射宽度", a.width, b.width, delta=a.width - b.width, tolerance=0.0)
    rep.add("正射高度", a.height, b.height, delta=a.height - b.height, tolerance=0.0)
    rep.add("正射波段数", a.count, b.count)
    rep.add("正射位深", a.dtype, b.dtype)
    for i, label in enumerate(("左", "下", "右", "上")):
        rep.add(
            f"正射范围{label} (m)",
            a.bounds[i],
            b.bounds[i],
            delta=a.bounds[i] - b.bounds[i],
            tolerance=1e-4,
        )

    same_grid = (
        a.width == b.width
        and a.height == b.height
        and abs(a.gsd - b.gsd) < 1e-9
        and abs(a.bounds[0] - b.bounds[0]) < 1e-4
        and abs(a.bounds[3] - b.bounds[3]) < 1e-4
    )
    if same_grid:
        with rasterio.open(ours) as o, rasterio.open(theirs) as t:
            oa = o.read(4) > 0 if o.count >= 4 else o.read(1) > 0
            ta = t.read(4) > 0 if t.count >= 4 else t.read(1) > 0
            both = oa & ta
            extra = oa & ~ta
            missing = ta & ~oa
            # 商业有效区可以大于本次相片足迹。掩膜不一致只记面积，不按逐格相同判失败。
            ha = (a.gsd ** 2) / 10000.0
            n_extra = int(extra.sum())
            n_miss = int(missing.sum())
            rep.add(
                "正射多余像元",
                n_extra,
                0,
                note=f"自研有、商业无，约 {n_extra * ha:.3f} ha；不按商业掩膜逐格判失败",
            )
            rep.add(
                "正射缺失像元",
                n_miss,
                0,
                note=f"商业有、自研无，约 {n_miss * ha:.3f} ha；不按商业掩膜逐格判失败",
            )
            if both.sum() >= 1000:
                for bi, name in enumerate(("R", "G", "B"), start=1):
                    ov = o.read(bi)[both].astype(np.float64)
                    tv = t.read(bi)[both].astype(np.float64)
                    mae = float(np.mean(np.abs(ov - tv)))
                    ratio = float(ov.mean() / max(tv.mean(), 1e-6))
                    corr = float(np.corrcoef(ov, tv)[0, 1])
                    rep.add(f"正射{name}均值", float(ov.mean()), float(tv.mean()),
                            delta=float(ov.mean() - tv.mean()), tolerance=8.0)
                    rep.add(f"正射{name} MAE", mae, 0.0, delta=mae, tolerance=25.0)
                    rep.add(
                        f"正射{name} 相关系数",
                        corr,
                        1.0,
                        delta=_corr_shortfall(corr, 0.85),
                        tolerance=0.0,
                        note="要求 r≥0.85，差值为低于门槛的缺口",
                    )
                    rep.add(f"正射{name} 亮度比", ratio, 1.0, note="自研/商业")
        return rep

    ours_on_b = _read_on_grid(ours, theirs)
    with rasterio.open(theirs) as src:
        theirs_arr = src.read(1).astype(np.float32)
        alpha = src.read(4) > 0 if src.count >= 4 else theirs_arr > 0
    both = np.isfinite(ours_on_b) & alpha
    if both.sum() < 1000:
        rep.add("正射灰度比对", None, None, note="重叠像元不足，无法比对")
        return rep
    corr = float(np.corrcoef(ours_on_b[both], theirs_arr[both])[0, 1])
    rep.add(
        "正射灰度相关系数",
        corr,
        1.0,
        delta=_corr_shortfall(corr, 0.85),
        tolerance=0.0,
        note="要求 r≥0.85，差值为低于门槛的缺口；格网未对齐，已重采样",
    )
    return rep


def geometric_shift(ours: Path, theirs: Path, *, max_shift_px: int = 40) -> tuple[float, float, float]:
    """用相位相关估计两幅正射的整体平移量（米）与峰值响应。

    平移量直接对应平面精度：若明显大于 1~2 个 GSD，说明空三或 DSM 有系统偏差。
    """
    import rasterio
    from scipy.signal import fftconvolve

    ours_on_b = _read_on_grid(ours, theirs)
    with rasterio.open(theirs) as src:
        theirs_arr = _band_as_float(src, 1)
        gsd = float(src.transform.a)

    both = np.isfinite(ours_on_b) & np.isfinite(theirs_arr)
    if both.sum() < 1000:
        return (float("nan"), float("nan"), float("nan"))
    rows = np.where(both.any(axis=1))[0]
    cols = np.where(both.any(axis=0))[0]
    sl = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
    a = np.nan_to_num(ours_on_b[sl] - np.nanmean(ours_on_b[sl]))
    b = np.nan_to_num(theirs_arr[sl] - np.nanmean(theirs_arr[sl]))
    resp = fftconvolve(a, b[::-1, ::-1], mode="same")
    peak = np.unravel_index(int(np.argmax(resp)), resp.shape)
    dy = peak[0] - resp.shape[0] // 2
    dx = peak[1] - resp.shape[1] // 2
    if abs(dy) > max_shift_px or abs(dx) > max_shift_px:
        return (float("nan"), float("nan"), float(resp.max()))
    return (dx * gsd, -dy * gsd, float(resp.max()))


def compare_geotiff_profile(ours: Path, theirs: Path, prefix: str, report: ComparisonReport) -> None:
    """文件级规格：压缩、是否分块、nodata、interleave。必须和商业成品一致。"""
    import rasterio

    with rasterio.open(ours) as a, rasterio.open(theirs) as b:
        # 商业成品是 scanline（blockysize=1）。rasterio.is_tiled 对整幅一块的 TIFF 不可靠。
        a_tile = int(a.profile.get("blockysize") or 1) > 1
        b_tile = int(b.profile.get("blockysize") or 1) > 1
        report.add(
            f"{prefix} tiled",
            a_tile,
            b_tile,
            delta=float(a_tile) - float(b_tile),
            tolerance=0.0,
            note="blockysize>1 视为 tiled",
        )
        ac = (a.compression.name if a.compression else "none").lower()
        bc = (b.compression.name if b.compression else "none").lower()
        report.add(f"{prefix} 压缩", ac, bc, delta=0.0 if ac == bc else 1.0, tolerance=0.0)
        ai = str(a.profile.get("interleave", "")).lower()
        bi = str(b.profile.get("interleave", "")).lower()
        report.add(f"{prefix} interleave", ai, bi, delta=0.0 if ai == bi else 1.0, tolerance=0.0)
        an = a.nodata
        bn = b.nodata
        if an is None and bn is None:
            report.add(f"{prefix} nodata", "None", "None", delta=0.0, tolerance=0.0)
        elif an is None or bn is None:
            report.add(f"{prefix} nodata", an, bn, delta=1.0, tolerance=0.0)
        else:
            report.add(f"{prefix} nodata", float(an), float(bn), delta=float(an) - float(bn), tolerance=1.0)


def write_delivery_report(ours_dir: Path, theirs_dir: Path, out_path: Path) -> ComparisonReport:
    """一次交付对照商业「拼图结果」写出 Markdown 比对表。未达标项会标「未达标」。"""
    from ms_mosaic.products import GROUP_PREFIX

    ours_dir = Path(ours_dir)
    theirs_dir = Path(theirs_dir)
    rep = ComparisonReport()
    dsm_o, dsm_t = ours_dir / "DSM.tif", theirs_dir / "DSM.tif"
    if dsm_o.exists() and dsm_t.exists():
        compare_dsm(dsm_o, dsm_t, report=rep)
        compare_geotiff_profile(dsm_o, dsm_t, "DSM", rep)
    rgb_o = ours_dir / f"{GROUP_PREFIX}0.tif"
    rgb_t = theirs_dir / f"{GROUP_PREFIX}0.tif"
    if rgb_o.exists() and rgb_t.exists():
        compare_ortho(rgb_o, rgb_t, report=rep)
        compare_geotiff_profile(rgb_o, rgb_t, "RGB", rep)
        dx, dy, _ = geometric_shift(rgb_o, rgb_t)
        if np.isfinite(dx):
            rep.add("正射平面偏移 X (m)", dx, 0.0, delta=dx, tolerance=0.11, note="约 2 个 GSD")
            rep.add("正射平面偏移 Y (m)", dy, 0.0, delta=dy, tolerance=0.11)
    for i in range(1, 8):
        a = ours_dir / f"{GROUP_PREFIX}{i}.tif"
        b = theirs_dir / f"{GROUP_PREFIX}{i}.tif"
        if a.exists() and b.exists():
            compare_geotiff_profile(a, b, f"MS{i}", rep)
            ra, rb = RasterRef.open(a), RasterRef.open(b)
            rep.add(f"MS{i} 宽度", ra.width, rb.width, delta=ra.width - rb.width, tolerance=0.0)
            rep.add(f"MS{i} GSD (m)", ra.gsd, rb.gsd, delta=ra.gsd - rb.gsd, tolerance=1e-9)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = rep.to_text()
    if not rep.ok:
        text += "\n\n结论：未达标。按「未达标」行反推对应模块再改，不要另起一套未文献化的算法。"
    else:
        text += "\n\n结论：规格与重叠区指标均达标。"
    out_path.write_text(text, encoding="utf-8")
    return rep
