"""成果输出的测试。重点是「规格逐项对齐商业成品」，这些是硬约束。"""

import json

import numpy as np
import pytest
import rasterio

from ms_mosaic.grid import Grid, erode_coverage, inpaint_nearest, refine_coverage_mask
from ms_mosaic.products import (
    GROUP_PREFIX,
    MS_BANDS,
    RGB_BAND,
    RasterWriter,
    apply_coverage_mask,
    band_count,
    band_dtype,
    clip_products_to_reference,
    group_name,
    match_lowfreq_to_reference,
    pseudocolor_dsm,
    quantize,
    rgb_with_alpha,
    seamline_geojson,
    write_kml,
    write_pseudocolor,
)

CRS = "EPSG:32647"
ORTHO_GSD = 0.053873647


def _grid(n=64, gsd=ORTHO_GSD):
    return Grid.from_bounds(
        (674000.0, 2620000.0, 674000.0 + n * gsd, 2620000.0 + n * gsd), gsd, CRS
    )


def test_group_names_match_commercial_layout():
    """group0 是 RGB，group1~7 按波长顺序对应 7 个多光谱波段。"""
    assert group_name(RGB_BAND) == "Orthomosaic_pix_surf_group0.tif"
    assert group_name("450nm") == "Orthomosaic_pix_surf_group1.tif"
    assert group_name("850nm") == "Orthomosaic_pix_surf_group7.tif"
    assert len(MS_BANDS) == 7
    assert [group_name(b) for b in MS_BANDS] == [
        f"Orthomosaic_pix_surf_group{i}.tif" for i in range(1, 8)
    ]


def test_group_name_rejects_unknown_band():
    with pytest.raises(ValueError, match="未知波段"):
        group_name("999nm")


def test_group_name_follows_the_job_band_list():
    """传入这次测区的波段表后，510nm、以及几十个波长都能编号。"""
    from ms_mosaic.scene import ordered_bands

    order = ordered_bands(["850nm", "Color", "510nm", "450nm"])
    assert order == ("Color", "450nm", "510nm", "850nm")
    assert group_name("510nm", order) == "Orthomosaic_pix_surf_group2.tif"
    assert group_name("850nm", order) == "Orthomosaic_pix_surf_group3.tif"
    dozens = [f"{400 + 10 * i}nm" for i in range(30)]
    order30 = ordered_bands(["Color", *dozens])
    assert len(order30) == 31
    assert group_name("400nm", order30) == "Orthomosaic_pix_surf_group1.tif"
    assert group_name("690nm", order30) == "Orthomosaic_pix_surf_group30.tif"
    assert band_dtype("510nm") == "uint16" and band_count("510nm") == 1


def test_band_dtype_and_count_match_commercial():
    assert band_dtype(RGB_BAND) == "uint8" and band_count(RGB_BAND) == 4
    for b in MS_BANDS:
        assert band_dtype(b) == "uint16" and band_count(b) == 1


def test_raster_writer_matches_commercial_ms_profile(tmp_path):
    grid = _grid(128)
    p = tmp_path / group_name("550nm")
    with RasterWriter(p, grid, count=1, dtype="uint16") as w:
        w.write((0, 0, grid.height, grid.width),
                np.full((1, grid.height, grid.width), 1234, np.uint16))
    with rasterio.open(p) as s:
        assert s.count == 1 and s.dtypes[0] == "uint16"
        assert s.compression.name.lower() == "lzw"
        assert str(s.crs) == CRS
        assert s.transform.a == pytest.approx(ORTHO_GSD)
        assert s.tags()["AREA_OR_POINT"] == "Area"
        assert s.nodata is None  # 商业成品不设 nodata
        assert s.read(1)[0, 0] == 1234


def test_raster_writer_rgb_has_alpha_band(tmp_path):
    from rasterio.enums import ColorInterp

    grid = _grid(64)
    p = tmp_path / group_name(RGB_BAND)
    data = np.zeros((4, grid.height, grid.width), np.uint8)
    data[3] = 255
    with RasterWriter(p, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, grid.height, grid.width), data)
    with rasterio.open(p) as s:
        assert s.count == 4 and s.dtypes[0] == "uint8"
        assert s.colorinterp[3] == ColorInterp.alpha
        assert [c.name for c in s.colorinterp[:3]] == ["red", "green", "blue"]
        assert int(s.profile.get("blockysize") or 1) == 1


def test_raster_writer_windowed_writes_assemble(tmp_path):
    """按块写入必须拼回一幅完整正确的影像。"""
    grid = _grid(96)
    p = tmp_path / "tiled.tif"
    with RasterWriter(p, grid, count=1, dtype="uint16") as w:
        for r0, c0, rows, cols in grid.tiles(32):
            # 每块填一个能反查出块号的值（须落在 uint16 范围内）
            block = np.full((1, rows, cols), (r0 // 32) * 10 + c0 // 32, np.uint16)
            w.write((r0, c0, rows, cols), block)
    with rasterio.open(p) as s:
        arr = s.read(1)
    assert arr[0, 0] == 0       # 第 0 行第 0 列块
    assert arr[40, 8] == 10     # 第 1 行第 0 列块
    assert arr[70, 70] == 22    # 第 2 行第 2 列块


def test_quantize_rgb_clips_and_masks():
    m = np.stack([np.array([[10.0, 300.0], [-5.0, np.nan]])] * 3)
    out, valid = quantize(m, RGB_BAND)
    assert out.dtype == np.uint8
    assert out[0, 0, 0] == 10 and out[0, 0, 1] == 255 and out[0, 1, 0] == 0
    assert valid[0, 0] and not valid[1, 1]


def test_quantize_ms_keeps_16bit_range():
    m = np.array([[[1000.0, 70000.0]]])
    out, _ = quantize(m, "550nm")
    assert out.dtype == np.uint16
    assert out[0, 0, 0] == 1000 and out[0, 0, 1] == 65535


def test_quantize_applies_scale():
    m = np.array([[[100.0]]])
    out, _ = quantize(m, "550nm", scale=2.5)
    assert out[0, 0, 0] == 250


def test_rgb_with_alpha_builds_four_bands():
    rgb = np.full((3, 4, 4), 128, np.uint8)
    valid = np.ones((4, 4), bool)
    valid[0, 0] = False
    out = rgb_with_alpha(rgb, valid)
    assert out.shape == (4, 4, 4)
    assert out[3, 0, 0] == 0 and out[3, 2, 2] == 255


def test_rgb_with_alpha_expands_single_channel():
    out = rgb_with_alpha(np.full((1, 3, 3), 77, np.uint8), np.ones((3, 3), bool))
    assert out.shape == (4, 3, 3)
    assert (out[:3] == 77).all()


def test_write_kml_contains_wgs84_ring(tmp_path):
    grid = _grid(200)
    p = write_kml(grid, tmp_path / "area.kml")
    text = p.read_text(encoding="utf-8")
    assert "<kml" in text and "LinearRing" in text
    coords = text.split("<coordinates>")[1].split("</coordinates>")[0].split()
    assert len(coords) == 5  # 四角闭合
    lon, lat, _ = coords[0].split(",")
    # UTM 47N 的这片区域应当落在云南一带
    assert 99.0 < float(lon) < 102.0 and 22.0 < float(lat) < 25.0


def test_write_kml_with_overlay(tmp_path):
    grid = _grid(50)
    text = write_kml(grid, tmp_path / "o.kml", image="ortho.png").read_text(encoding="utf-8")
    assert "GroundOverlay" in text and "ortho.png" in text
    assert "<north>" in text and "<west>" in text


def test_pseudocolor_shape_and_nodata():
    z = np.full((20, 20), 1760.0)
    z[:, :5] = np.nan
    z[10:, 10:] = 1790.0
    rgb = pseudocolor_dsm(z)
    assert rgb.shape == (3, 20, 20) and rgb.dtype == np.uint8
    assert (rgb[:, :, :5] == 0).all()          # 无数据区涂黑
    assert rgb[:, 15, 15].sum() != rgb[:, 2, 8].sum()  # 不同高程颜色不同


def test_pseudocolor_hillshade_reveals_slope():
    """同一高程但不同坡向的两块，叠山体阴影后亮度应当不同。"""
    y, x = np.indices((40, 40))
    z = 1760.0 + 0.5 * x
    flat = np.full((40, 40), 1760.0 + 0.5 * 20)
    shaded = pseudocolor_dsm(z).astype(float).mean(axis=0)
    plain = pseudocolor_dsm(flat, hillshade=False).astype(float).mean(axis=0)
    assert np.std(shaded) > np.std(plain)


def test_pseudocolor_all_nan():
    assert (pseudocolor_dsm(np.full((6, 6), np.nan)) == 0).all()


def test_write_pseudocolor_geotiff(tmp_path):
    grid = _grid(32)
    z = np.random.default_rng(0).normal(1760, 5, grid.shape)
    p = write_pseudocolor(z, grid, tmp_path / "dsm_rgb.tif")
    with rasterio.open(p) as s:
        assert s.count == 3 and s.dtypes[0] == "uint8"
        assert str(s.crs) == CRS


def test_seamline_geojson_has_polygon_per_view(tmp_path):
    grid = _grid(60)
    labels = np.zeros(grid.shape, np.int16)
    labels[:, 30:] = 1
    labels[:5, :5] = -1
    p = seamline_geojson(labels, grid, [101, 202], tmp_path / "seams.geojson")
    fc = json.loads(p.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    ids = sorted(f["properties"]["image_id"] for f in fc["features"])
    assert ids == [101, 202]
    for f in fc["features"]:
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert f["properties"]["area_m2"] > 0
        lon, lat = f["geometry"]["coordinates"][0][0][:2]
        assert 99.0 < lon < 102.0 and 22.0 < lat < 25.0


def test_seamline_geojson_drops_slivers(tmp_path):
    grid = _grid(60)
    labels = np.zeros(grid.shape, np.int16)
    labels[0, 0] = 1  # 单格碎片，应被过滤
    p = seamline_geojson(labels, grid, [1, 2], tmp_path / "s.geojson")
    fc = json.loads(p.read_text(encoding="utf-8"))
    assert all(f["properties"]["image_id"] != 2 for f in fc["features"])


def test_seamline_geojson_empty(tmp_path):
    grid = _grid(20)
    p = seamline_geojson(np.full(grid.shape, -1, np.int16), grid, [], tmp_path / "e.geojson")
    assert json.loads(p.read_text(encoding="utf-8"))["features"] == []


def test_refine_coverage_mask_drops_island_and_fills_speckle():
    valid = np.zeros((80, 80), bool)
    valid[20:50, 20:50] = True
    valid[30, 30] = False  # 主体内部 1 像素孔 → 白点
    valid[0:3, 76:80] = True  # 远离主体的游离斑（绿斑）
    out = refine_coverage_mask(valid, max_hole_cells=16, merge_gap_cells=8)
    assert out[30, 30]
    assert not out[1, 78]
    assert out[35, 35]


def test_refine_coverage_mask_keeps_large_separate_region():
    """被水道隔开、但本身够大的另一块覆盖要留下；远处几像素的绿斑仍丢掉。"""
    valid = np.zeros((80, 80), bool)
    valid[5:40, 5:30] = True
    valid[5:40, 50:75] = True
    valid[0:2, 78:80] = True
    out = refine_coverage_mask(valid, max_hole_cells=0, merge_gap_cells=4, min_island_cells=64)
    assert out[20, 15]
    assert out[20, 60]
    assert not out[0, 78]


def test_refine_coverage_mask_merges_near_peninsula():
    """只隔数像素的碎块是被白点切断的条带边缘，商业成品里仍然有覆盖。"""
    valid = np.zeros((40, 40), bool)
    valid[5:30, 5:30] = True
    valid[32:36, 10:20] = True  # 与主体相隔 2 像素
    out = refine_coverage_mask(valid, max_hole_cells=0, merge_gap_cells=8, min_island_cells=16)
    assert out[33, 15]
    assert out[20, 20]


def test_inpaint_nearest_copies_neighbor():
    data = np.zeros((2, 5, 5), np.uint8)
    data[:, 2, 2] = 80
    known = np.zeros((5, 5), bool)
    known[2, 2] = True
    take = known.copy()
    take[2, 3] = True
    out = inpaint_nearest(data, known, take)
    assert out[0, 2, 3] == 80


def test_match_lowfreq_replaces_base_keeps_detail(tmp_path):
    """大尺度色差跟参考走，高频纹理留下。"""
    from rasterio.transform import Affine

    gsd = 1.0
    grid = Grid(Affine(gsd, 0.0, 0.0, 0.0, -gsd, 32.0), 32, 32, CRS)
    ours = np.full((3, 32, 32), 40.0, np.float32)
    ours[:, 8:12, 8:12] += 80.0  # 局部纹理
    ours[:, :, :16] += 30.0  # 左半边整块偏亮（色斑）
    ref = np.full((3, 32, 32), 80.0, np.float32)
    path = tmp_path / "ref.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=32, height=32, count=4, dtype="uint8",
        crs=CRS, transform=grid.transform,
    ) as ds:
        rgb = np.clip(ref, 0, 255).astype(np.uint8)
        ds.write(rgb[0], 1)
        ds.write(rgb[1], 2)
        ds.write(rgb[2], 3)
        ds.write(np.full((32, 32), 255, np.uint8), 4)
    out = match_lowfreq_to_reference(ours, grid, path, sigma_m=4.0)
    # 左半边不应再比右半边亮几十档
    assert abs(float(out[1, 20, 8]) - float(out[1, 20, 24])) < 15
    # 小块纹理相对邻域仍然在
    assert float(out[1, 10, 10]) - float(out[1, 4, 4]) > 30
    # 10 格色带：σ=40 m 当细节留下，默认 σ=2.5 m 进低频被换成参考色
    big = Grid(Affine(gsd, 0.0, 0.0, 0.0, -gsd, 64.0), 64, 64, CRS)
    strip = np.full((3, 64, 64), 50.0, np.float32)
    strip[:, :, 20:30] += 40.0
    ref2 = np.full((3, 64, 64), 80.0, np.float32)
    path2 = tmp_path / "ref2.tif"
    with rasterio.open(
        path2, "w", driver="GTiff", width=64, height=64, count=4, dtype="uint8",
        crs=CRS, transform=big.transform,
    ) as ds:
        ds.write(np.clip(ref2, 0, 255).astype(np.uint8)[0], 1)
        ds.write(np.clip(ref2, 0, 255).astype(np.uint8)[1], 2)
        ds.write(np.clip(ref2, 0, 255).astype(np.uint8)[2], 3)
        ds.write(np.full((64, 64), 255, np.uint8), 4)
    out_hi = match_lowfreq_to_reference(strip, big, path2, sigma_m=40.0)
    out_lo = match_lowfreq_to_reference(strip, big, path2)
    assert abs(float(out_hi[1, 32, 25]) - float(out_hi[1, 32, 48])) > 15
    assert abs(float(out_lo[1, 32, 25]) - float(out_lo[1, 32, 48])) < 12


def test_apply_coverage_mask_fills_interior_rgb_zero():
    """内部 RGB 全 0 是阴影或未采样小孔，必须补上。打成透明再腐蚀会扩成菱形白洞。"""
    rgb = np.full((3, 24, 24), 80, np.uint8)
    rgb[:, 8:16, 8:16] = 0
    valid = np.ones((24, 24), bool)
    out, take = apply_coverage_mask(rgb, valid, max_hole_cells=8)
    assert take[12, 12]
    assert take[4, 4] and out[0, 4, 4] == 80
    assert out[0, 12, 12] == 80


def test_apply_coverage_mask_trims_outer_edge_only():
    """收边只削外轮廓。内部 1 格孔不得被 trim 扩成大洞。"""
    rgb = np.full((3, 40, 40), 70, np.uint8)
    rgb[:, 20, 20] = 0
    valid = np.ones((40, 40), bool)
    out, take = apply_coverage_mask(rgb, valid, trim_m=3.0, gsd=1.0, max_hole_cells=0)
    assert take[20, 20]
    assert take[15, 15]
    assert not take[0, 20] and not take[20, 0]
    assert (out[:, 0, 20] == 0).all()


def test_erode_coverage_does_not_grow_interior_holes():
    """4 连通腐蚀若从内部孔往外扩，20 m 收边会在林里打出菱形白洞。"""
    mask = np.ones((40, 40), bool)
    mask[20, 20] = False
    out = erode_coverage(mask, gsd=1.0, trim_m=3.0)
    assert out[20, 20]
    assert out[18, 20] and out[20, 18]
    assert not out[0, 20] and not out[20, 0]


def test_write_band_product_does_not_paint_empty_cells_opaque(tmp_path):
    """外轮廓用自身有色像元，缺的格子保持透明，禁止按参考剪影像再涂黑。"""
    from ms_mosaic.compose import write_band_product

    grid = _grid(32)
    mosaic = np.full((3, 32, 32), 40.0, np.float32)
    mosaic[:, :8, :] = np.nan
    mosaic[:, 28:, 28:] = 90.0
    path = tmp_path / group_name(RGB_BAND)
    write_band_product(mosaic, grid, RGB_BAND, path, trim_m=0.0)
    with rasterio.open(path) as ds:
        rgb = ds.read()
    assert rgb[3, 6, 16] == 0
    assert rgb[3, 16, 16] == 255
    assert rgb[0, 16, 16] == 40
    assert rgb[3, 30, 30] == 255


def test_parallel_band_writes_match_serial_pixels(tmp_path):
    """几个波段同时写，像元、压缩、格网与逐个写逐项相同。"""
    from concurrent.futures import ThreadPoolExecutor

    from ms_mosaic.compose import write_band_product

    grid = _grid(48)
    rng = np.random.default_rng(4)
    bands = ("450nm", "550nm", "650nm", "720nm")
    arrays = []
    for _ in bands:
        mosaic = rng.integers(20, 800, size=(1, 48, 48)).astype(np.float32)
        mosaic[:, :5, :] = np.nan
        mosaic[:, 12:16, 8:20] = np.nan
        arrays.append(mosaic)
    serial = tmp_path / "serial"
    parallel = tmp_path / "parallel"
    serial.mkdir()
    parallel.mkdir()
    for band, mosaic in zip(bands, arrays):
        write_band_product(mosaic, grid, band, serial / group_name(band))

    def one(i: int) -> None:
        with rasterio.Env():
            write_band_product(arrays[i], grid, bands[i], parallel / group_name(bands[i]))

    with ThreadPoolExecutor(max_workers=len(bands)) as pool:
        list(pool.map(one, range(len(bands))))

    for band in bands:
        with rasterio.open(serial / group_name(band)) as a, rasterio.open(parallel / group_name(band)) as b:
            np.testing.assert_array_equal(a.read(), b.read())
            assert a.profile["compress"] == "lzw" and b.profile["compress"] == "lzw"
            assert a.profile["tiled"] is False and b.profile["tiled"] is False
            assert a.count == 1 and a.dtypes[0] == "uint16"
            assert a.crs == b.crs
            assert a.transform == b.transform
            assert a.profile["interleave"] == b.profile["interleave"] == "band"


def test_clip_products_does_not_copy_reference_alpha(tmp_path):
    """对标只抽参数。把参考 alpha 套到未采样格上会留下不透明黑底。"""
    ours = tmp_path / "ours"
    ref = tmp_path / "ref"
    ours.mkdir()
    ref.mkdir()
    grid = _grid(32, gsd=1.0)
    rgb = np.zeros((4, 32, 32), np.uint8)
    rgb[:3, 8:24, 8:24] = 90
    rgb[3] = 255
    rgb_path = ours / f"{GROUP_PREFIX}0.tif"
    with RasterWriter(rgb_path, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, 32, 32), rgb)
    ref_rgb = np.zeros((4, 32, 32), np.uint8)
    ref_rgb[:3, 4:28, 4:28] = 40
    ref_rgb[3, 4:28, 4:28] = 255
    with RasterWriter(ref / f"{GROUP_PREFIX}0.tif", grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, 32, 32), ref_rgb)
    clip_products_to_reference(ours, ref, trim_m=0.0)
    with rasterio.open(rgb_path) as ds:
        got = ds.read()
    assert got[3, 16, 16] == 255
    assert got[0, 16, 16] == 90
    assert got[3, 6, 16] == 0
    assert (got[:3, 6, 16] == 0).all()
