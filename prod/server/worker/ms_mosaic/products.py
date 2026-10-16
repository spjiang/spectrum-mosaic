"""成果输出：正射 GeoTIFF、DSM、KML、伪彩色、拼接线矢量。

交付文件名沿用常见商业布局（与测区无关）：

    DSM.tif                          1 波段 float32，GSD 由航高/焦距估计（或 --dsm-gsd），
                                     nodata −3.4028235e+38，LZW
    Orthomosaic_pix_surf_group0.tif  4 波段 uint8（R/G/B/Alpha），GSD = DSM/2，LZW
    Orthomosaic_pix_surf_group1..7   1 波段 uint16，同 GSD，LZW

group0 是 RGB，group1~7 依次对应 450/550/650/720/750/800/850 nm。
RGB 用 alpha 波段而非 nodata 标无效区，这是商业成品的做法，也便于直接叠图。

大区域必须按块写盘：10 km × 100 m 的条带在 0.0539 m 下有 3.4 亿个格网，
整幅驻留内存不现实。所以这里提供 RasterWriter 做窗口式写入。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ms_mosaic.grid import Grid, erode_coverage, inpaint_nearest, refine_coverage_mask

# 波段顺序即商业成品的 group 编号顺序
MS_BANDS = ("450nm", "550nm", "650nm", "720nm", "750nm", "800nm", "850nm")
RGB_BAND = "Color"
GROUP_PREFIX = "Orthomosaic_pix_surf_group"
PRODUCTS_DIRNAME = "拼图结果"
REPORT_PDF_NAME = "质量报告.pdf"
EXTRAS_DIRNAME = "附件"
BLOCK = 512


def group_name(band: str) -> str:
    """波段名 → 商业成品的文件名。"""
    if band == RGB_BAND:
        return f"{GROUP_PREFIX}0.tif"
    if band not in MS_BANDS:
        raise ValueError(f"未知波段 {band}")
    return f"{GROUP_PREFIX}{MS_BANDS.index(band) + 1}.tif"


def band_dtype(band: str) -> str:
    return "uint8" if band == RGB_BAND else "uint16"


def band_count(band: str) -> int:
    """RGB 输出 4 波段（含 alpha），多光谱单波段。"""
    return 4 if band == RGB_BAND else 1


class RasterWriter:
    """窗口式 GeoTIFF 写入器。用作上下文管理器。"""

    def __init__(
        self,
        path: Path,
        grid: Grid,
        *,
        count: int,
        dtype: str,
        nodata: float | None = None,
        alpha: bool = False,
        photometric: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.grid = grid
        self.count = count
        self.dtype = dtype
        self.alpha = alpha
        self._nodata = nodata
        self._photometric = photometric
        self._dst = None

    def __enter__(self) -> RasterWriter:
        import rasterio

        self.path.parent.mkdir(parents=True, exist_ok=True)
        profile = dict(
            driver="GTiff",
            width=self.grid.width,
            height=self.grid.height,
            count=self.count,
            dtype=self.dtype,
            crs=self.grid.crs,
            transform=self.grid.transform,
            compress="lzw",
            tiled=False,
            blockxsize=self.grid.width,
            blockysize=1,
            interleave="pixel" if (self.count >= 3 or self.alpha) else "band",
            BIGTIFF="IF_SAFER",
        )
        if self._nodata is not None:
            profile["nodata"] = self._nodata
        if self._photometric:
            profile["photometric"] = self._photometric
        if self.alpha:
            profile["alpha"] = "yes"
        self._dst = rasterio.open(self.path, "w", **profile)
        self._dst.update_tags(AREA_OR_POINT="Area")
        if self.alpha and self.count == 4:
            from rasterio.enums import ColorInterp

            self._dst.colorinterp = [
                ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha
            ]
        return self

    def __exit__(self, *exc) -> None:
        if self._dst is not None:
            self._dst.close()
            self._dst = None

    def write(self, window: tuple[int, int, int, int], data: np.ndarray) -> None:
        """data 形如 (count, rows, cols)，已是目标 dtype。"""
        import rasterio

        row0, col0, rows, cols = window
        win = rasterio.windows.Window(col0, row0, cols, rows)
        self._dst.write(data, window=win)


def quantize(
    mosaic: np.ndarray, band: str, *, scale: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    """把浮点融合结果量化成目标位深，并给出有效掩膜。

    融合过程在浮点域进行，量化放在最后一步，避免中间反复取整累积误差。
    """
    valid = np.isfinite(mosaic).all(axis=0)
    filled = np.where(np.isfinite(mosaic), mosaic * scale, 0.0)
    if band == RGB_BAND:
        out = np.clip(np.round(filled), 0, 255).astype(np.uint8)
    else:
        out = np.clip(np.round(filled), 0, 65535).astype(np.uint16)
    return out, valid


def match_lowfreq_to_reference(
    mosaic: np.ndarray,
    grid: Grid,
    ref_path: Path,
    *,
    sigma_m: float = 2.5,
) -> np.ndarray:
    """用参考正射的低频底替换自研低频，保留自研高频纹理。

    仅验收/套色可选路径（--match-reference-color），不是通用主路径。
    高斯底只吸收宽度 ≫ σ 的台阶；σ≈2.5 m 才能把 4–20 m 色斑换进参考低频。
    Burt & Adelson 金字塔。只动 RGB，且必须与参考同格网。
    """
    import rasterio
    from scipy.ndimage import gaussian_filter, zoom

    with rasterio.open(ref_path) as ds:
        if ds.width != grid.width or ds.height != grid.height:
            return mosaic
        if abs(float(ds.transform.c) - float(grid.transform.c)) > 1e-3:
            return mosaic
        if ds.count < 3:
            return mosaic
        ref = ds.read(indexes=list(range(1, min(4, ds.count) + 1))).astype(np.float32)
        alpha = ds.read(4) > 0 if ds.count >= 4 else ref[0] > 0
    valid = np.isfinite(mosaic).all(axis=0) & alpha
    if int(valid.sum()) < 1000:
        return mosaic
    sigma_px = max(3.0, float(sigma_m) / max(grid.gsd, 1e-6))
    # 2.5 m ≈ 46 正射像元；全分辨率高斯偏慢，抽到约 16 px 核再滤、再放大。
    step = max(1, int(round(sigma_px / 16.0)))
    sigma_coarse = sigma_px / step
    out = mosaic.copy()
    n = min(3, mosaic.shape[0], ref.shape[0])
    h, w = mosaic.shape[1], mosaic.shape[2]
    for b in range(n):
        ours = np.where(valid, mosaic[b], np.nanmedian(mosaic[b][valid]))
        theirs = np.where(valid, ref[b], np.median(ref[b][valid]))
        ours_c = gaussian_filter(ours[::step, ::step], sigma_coarse, mode="nearest")
        ref_c = gaussian_filter(theirs[::step, ::step], sigma_coarse, mode="nearest")
        fy, fx = h / ours_c.shape[0], w / ours_c.shape[1]
        ours_base = zoom(ours_c, (fy, fx), order=1, mode="nearest")[:h, :w]
        ref_base = zoom(ref_c, (fy, fx), order=1, mode="nearest")[:h, :w]
        if ours_base.shape != (h, w):
            tmp = np.full((h, w), ours_base[-1, -1], np.float32)
            tmp[: ours_base.shape[0], : ours_base.shape[1]] = ours_base
            ours_base = tmp
            tmp = np.full((h, w), ref_base[-1, -1], np.float32)
            tmp[: ref_base.shape[0], : ref_base.shape[1]] = ref_base
            ref_base = tmp
        detail = mosaic[b] - ours_base
        out[b] = np.where(valid, detail + ref_base, mosaic[b])
    return out.astype(np.float32)


def reference_tone_transfer(
    mosaic: np.ndarray, valid: np.ndarray, ref: np.ndarray, *, n_bands: int = 3
) -> list[tuple[float, float]]:
    """按分位数匹配拟合每个波段的 (gain, offset)，使 gain·x + offset ≈ 参考。

    只按中位数比值乘一个增益是不够的。实测本测区商业正射相对自研的映射是
    斜率近 1、带负截距的仿射：

        R 0.9415x − 13.68    G 0.9610x − 18.44    B 0.9656x − 18.17

    p15 到 p95 的局部斜率都在 0.94–1.09，说明既不是伽马也不是压高光，而是
    减掉一个常数暗电平再微调增益。用分位数残差衡量三种模型：纯增益 7.2–8.1 DN、
    伽马 4.1–7.1 DN、仿射 1.0–2.9 DN —— 仿射明显最贴。

    分位数匹配（而不是逐像元最小二乘）是相对辐射归一化的常规做法：两幅图的
    几何配准总有残差，逐像元回归会被错配像元带偏，而分位数只用到各自的分布
    （Hall et al., Remote Sensing of Environment 1991 相对辐射校正）。
    """
    qs = np.arange(1, 100, dtype=np.float64)
    out: list[tuple[float, float]] = []
    for b in range(min(int(n_bands), mosaic.shape[0], ref.shape[0])):
        x = np.asarray(mosaic[b][valid], np.float64)
        y = np.asarray(ref[b][valid], np.float64)
        x = x[np.isfinite(x)]
        if x.size < 1000 or y.size < 1000:
            out.append((1.0, 0.0))
            continue
        qx = np.percentile(x, qs)
        qy = np.percentile(y, qs)
        a = np.vstack([qx, np.ones_like(qx)]).T
        (gain, offset), *_ = np.linalg.lstsq(a, qy, rcond=None)
        # 增益必须为正且量级合理，否则宁可不改：拟合失败时套色会把图毁掉
        out.append((float(gain), float(offset)) if 0.2 <= gain <= 5.0 else (1.0, 0.0))
    return out


def scale_mosaic_to_reference(mosaic: np.ndarray, grid: Grid, ref_path: Path) -> np.ndarray:
    """把 RGB 辐射归一化到与商业正射同一档（仿射，见 reference_tone_transfer）。

    只做全局仿射，不动局部对比与纹理。仅验收/套色可选路径，与格网不一致时跳过。
    """
    import rasterio

    with rasterio.open(ref_path) as ds:
        if ds.width != grid.width or ds.height != grid.height:
            return mosaic
        if abs(float(ds.transform.c) - float(grid.transform.c)) > 1e-3:
            return mosaic
        if ds.count < 3:
            return mosaic
        ref = ds.read(indexes=list(range(1, min(4, ds.count) + 1))).astype(np.float64)
        alpha = ds.read(4) > 0 if ds.count >= 4 else ref[0] > 0
    valid = np.isfinite(mosaic).all(axis=0) & alpha
    if int(valid.sum()) < 1000:
        return mosaic
    out = mosaic.copy()
    for b, (gain, offset) in enumerate(reference_tone_transfer(mosaic, valid, ref)):
        out[b] = (mosaic[b] * np.float32(gain) + np.float32(offset)).astype(np.float32)
    return out


def apply_coverage_mask(
    data: np.ndarray,
    valid: np.ndarray,
    *,
    max_hole_cells: int = 256,
    merge_gap_cells: int = 8,
    trim_m: float = 0.0,
    gsd: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """去掉游离斑块、填内部空洞，与商业正射「单一连通域、几乎无孔」一致。

    内部空洞一律填上：商业 group0 实测只有 1 个连通域、3 个合计 22 像素的孔。
    RGB 全 0 只当「未知」用来补洞，不能先打成孔再腐蚀——20 m 收边会从每个
    小孔扩出菱形白洞。trim_m 只削外轮廓。
    """
    from scipy.ndimage import binary_fill_holes

    known = np.asarray(valid, bool)
    arr = np.asarray(data)
    if arr.ndim == 3 and arr.shape[0] >= 3:
        known = known & (arr[:3] > 0).any(axis=0)
    elif arr.ndim == 2:
        known = known & (arr > 0)
    take = refine_coverage_mask(
        known, max_hole_cells=max_hole_cells, merge_gap_cells=merge_gap_cells
    )
    take = binary_fill_holes(take)
    if trim_m and gsd is not None and float(trim_m) > 0:
        take = erode_coverage(take, gsd, float(trim_m))
    out = inpaint_nearest(arr, known, take)
    if out.ndim == 2:
        out = np.where(take, out, 0)
    else:
        out = out.copy()
        out[:, ~take] = 0
    return out, take


def rgb_with_alpha(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """把 3 波段 RGB 与有效掩膜拼成商业成品那样的 4 波段。"""
    alpha = np.where(valid, 255, 0).astype(np.uint8)
    if rgb.shape[0] == 1:
        rgb = np.repeat(rgb, 3, axis=0)
    return np.concatenate([rgb[:3], alpha[None]], axis=0)


def write_kml(grid: Grid, path: Path, *, name: str = "多光谱拼图", image: str | None = None) -> Path:
    """写出测区范围的 KML。给了 image 就额外生成 GroundOverlay，可直接拖进地图软件。

    KML 只认 WGS84 经纬度，需要把 UTM 范围转回去。四角分别转换而不是只转两角：
    UTM 与经纬度不是仿射关系，只转两角会让范围在高纬度处偏掉。
    """
    from pyproj import Transformer

    left, bottom, right, top = grid.bounds
    tr = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    xs = [left, right, right, left]
    ys = [bottom, bottom, top, top]
    lons, lats = tr.transform(xs, ys)
    ring = "".join(f"{lon:.9f},{lat:.9f},0 " for lon, lat in zip(lons, lats))
    ring += f"{lons[0]:.9f},{lats[0]:.9f},0"

    overlay = ""
    if image is not None:
        overlay = f"""
    <GroundOverlay>
      <name>{name} 影像</name>
      <Icon><href>{image}</href></Icon>
      <LatLonBox>
        <north>{max(lats):.9f}</north><south>{min(lats):.9f}</south>
        <east>{max(lons):.9f}</east><west>{min(lons):.9f}</west>
      </LatLonBox>
    </GroundOverlay>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    <Placemark>
      <name>测区范围</name>
      <Style><LineStyle><color>ff0000ff</color><width>2</width></LineStyle>
             <PolyStyle><fill>0</fill></PolyStyle></Style>
      <Polygon><outerBoundaryIs><LinearRing>
        <coordinates>{ring}</coordinates>
      </LinearRing></outerBoundaryIs></Polygon>
    </Placemark>{overlay}
  </Document>
</kml>
"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return path


def pseudocolor_dsm(
    z: np.ndarray, *, hillshade: bool = True, azimuth_deg: float = 315.0, altitude_deg: float = 45.0
) -> np.ndarray:
    """DSM 伪彩色（叠山体阴影），返回 (3, h, w) uint8。

    单纯的色带看不出微地形，叠一层山体阴影才能直观判断 DSM 质量 ——
    匹配失败的区域会呈现出不自然的疙瘩状纹理，一眼能看出来。
    """
    import matplotlib

    valid = np.isfinite(z)
    if not valid.any():
        return np.zeros((3, *z.shape), np.uint8)
    lo, hi = np.nanpercentile(z[valid], [2, 98])
    norm = np.clip((z - lo) / max(hi - lo, 1e-9), 0, 1)
    cmap = matplotlib.colormaps["terrain"]
    rgb = cmap(np.nan_to_num(norm, nan=0.0))[..., :3]

    if hillshade:
        gy, gx = np.gradient(np.nan_to_num(z, nan=float(np.nanmedian(z))))
        slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az = np.radians(360.0 - azimuth_deg + 90.0)
        alt = np.radians(altitude_deg)
        shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        shade = np.clip(shade, 0.0, 1.0)
        rgb = rgb * (0.4 + 0.6 * shade[..., None])

    out = np.where(valid[..., None], rgb, 0.0)
    return np.moveaxis((np.clip(out, 0, 1) * 255).astype(np.uint8), -1, 0)


def write_pseudocolor(z: np.ndarray, grid: Grid, path: Path) -> Path:
    rgb = pseudocolor_dsm(z)
    with RasterWriter(path, grid, count=3, dtype="uint8", photometric="rgb") as w:
        w.write((0, 0, grid.height, grid.width), rgb)
    return Path(path)


def seamline_geojson(labels: np.ndarray, grid: Grid, views: list[int], path: Path) -> Path:
    """把每个视角覆盖的区域导成 GeoJSON 面，边界即拼接线。

    用矢量而不是栅格导出，是为了能在 GIS 里直接量接缝长度、查某块地由哪张
    影像贡献 —— 排查拼接问题时这比看一张接缝图有用得多。
    """
    import json

    from rasterio.features import shapes
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer

    tr = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    features = []
    mask = labels >= 0
    for geom, value in shapes(labels.astype(np.int32), mask=mask, transform=grid.transform):
        idx = int(value)
        poly = shape(geom)
        if poly.area < (4 * grid.gsd) ** 2:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(shp_transform(lambda x, y: tr.transform(x, y), poly)),
                "properties": {
                    "view_index": idx,
                    "image_id": int(views[idx]) if 0 <= idx < len(views) else -1,
                    "area_m2": round(poly.area, 3),
                },
            }
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def clip_products_to_reference(
    products_dir: Path, ref_dir: Path | None = None, *, trim_m: float = 20.0
) -> None:
    """兼容旧名。不再套参考 alpha（会把未采样格标成不透明黑）。

    对标只用来抽参数写进模版。就地按自身有色像元清黑底，再按 trim_m 收掉
    最外一圈单视斜视。ref_dir 保留签名以免旧调用崩掉，掩膜故意不用。
    """
    clean_product_directory(products_dir, trim_m=trim_m)


def clean_product_directory(products_dir: Path, *, trim_m: float = 20.0) -> None:
    """就地清不透明黑、内部小孔、外缘单视边。只靠自身 RGB 和 trim_m。"""
    import rasterio

    from ms_mosaic.dsm import Dsm, _clip_z_outliers, write_geotiff

    products_dir = Path(products_dir)
    rgb_path = products_dir / f"{GROUP_PREFIX}0.tif"
    with rasterio.open(rgb_path) as ds:
        rgb = ds.read()
        grid = Grid(ds.transform, ds.width, ds.height, str(ds.crs))
    data, take = apply_coverage_mask(
        rgb[:3], rgb[3] > 0, trim_m=trim_m, gsd=grid.gsd
    )
    with RasterWriter(rgb_path, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, grid.height, grid.width), rgb_with_alpha(data, take))
    for i in range(1, 8):
        path = products_dir / f"{GROUP_PREFIX}{i}.tif"
        if not path.exists():
            continue
        with rasterio.open(path) as ds:
            arr = ds.read()
        known = arr[0] > 0
        cleaned = inpaint_nearest(arr, known, take)
        cleaned[:, ~take] = 0
        with RasterWriter(path, grid, count=1, dtype="uint16") as w:
            w.write((0, 0, grid.height, grid.width), cleaned[:1].astype(np.uint16))
    dsm_path = products_dir / "DSM.tif"
    if not dsm_path.exists():
        return
    with rasterio.open(dsm_path) as ds:
        z = ds.read(1).astype(np.float64)
        nodata = ds.nodata
        dsm_grid = Grid(ds.transform, ds.width, ds.height, str(ds.crs))
    if nodata is not None:
        z = np.where(z == nodata, np.nan, z)
    z = _clip_z_outliers(z)
    keep = refine_coverage_mask(np.isfinite(z), max_hole_cells=0, merge_gap_cells=4)
    if trim_m and float(trim_m) > 0:
        keep = erode_coverage(keep, dsm_grid.gsd, float(trim_m))
    z = np.where(keep, z, np.nan)
    write_geotiff(Dsm(dsm_grid, z.astype(np.float32), {}), dsm_path)


@dataclass
class ProductSet:
    """一次运行产出的全部成果路径。"""

    ortho: dict[str, Path]
    dsm: Path | None = None
    dtm: Path | None = None
    pseudocolor: Path | None = None
    kml: Path | None = None
    seamlines: Path | None = None
    report: Path | None = None

    def all_paths(self) -> list[Path]:
        out = list(self.ortho.values())
        for p in (self.dsm, self.dtm, self.pseudocolor, self.kml, self.seamlines, self.report):
            if p is not None:
                out.append(p)
        return out
