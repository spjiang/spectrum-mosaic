"""DSM 与比对工具的测试。"""

import numpy as np
import pytest

from ms_mosaic.compare import (
    ComparisonReport,
    RasterRef,
    compare_dsm,
    fit_plane_residual,
    geometric_shift,
)
from ms_mosaic.dense import HeightField
from ms_mosaic.dsm import (
    NODATA,
    build_dsm,
    clip_z_to_plausible,
    complete_dsm_coverage,
    fill_holes,
    median_smooth,
    plausible_z_limits,
    remove_spikes,
    resample_height,
    to_dtm,
    write_geotiff,
)
from ms_mosaic.grid import Grid

CRS = "EPSG:32647"


def _grid(n=40, gsd=0.1):
    return Grid.from_bounds((673000.0, 2620000.0, 673000.0 + n * gsd, 2620000.0 + n * gsd), gsd, CRS)


def test_remove_spikes_kills_isolated_outlier():
    z = np.full((30, 30), 1760.0)
    z[15, 15] = 1820.0
    out = remove_spikes(z)
    assert np.isnan(out[15, 15])
    assert np.isfinite(np.delete(out.ravel(), 15 * 30 + 15)).all()


def test_remove_spikes_keeps_building_edge():
    """一整块高出来的区域是建筑，不是粗差，必须保留。"""
    z = np.full((40, 40), 1760.0)
    z[12:28, 12:28] = 1772.0
    out = remove_spikes(z)
    # 块内部一定保留；仅允许边界一圈被误判
    assert np.isfinite(out[16:24, 16:24]).all()
    assert out[20, 20] == pytest.approx(1772.0)


def test_remove_spikes_keeps_smooth_slope():
    xs = np.arange(40)[None, :] * np.ones((40, 1))
    z = 1760.0 + 0.3 * xs
    out = remove_spikes(z)
    assert np.isfinite(out[3:-3, 3:-3]).all()


def test_median_smooth_kills_salt_keeps_building_and_holes():
    z = np.full((40, 40), 1760.0)
    z[12:28, 12:28] = 1772.0
    z[5, 5] = 1785.0  # 单格噪声
    z[2, 2] = np.nan
    out = median_smooth(z, cells=3)
    assert np.isnan(out[2, 2])
    assert out[5, 5] == pytest.approx(1760.0)
    assert out[20, 20] == pytest.approx(1772.0)


def test_fill_holes_reproduces_linear_slope_exactly():
    """调和插值在线性坡面上应当精确复原 —— 这正是「向内生长取均值」做不到的。"""
    xs = np.arange(30)[None, :] * np.ones((30, 1))
    truth = 1760.0 + 0.5 * xs
    z = truth.copy()
    z[12:18, 12:18] = np.nan
    out = fill_holes(z)
    assert np.isfinite(out).all()
    assert float(np.abs(out[12:18, 12:18] - truth[12:18, 12:18]).max()) < 1e-6


def test_fill_holes_obeys_maximum_principle():
    """调和插值不得在空洞内造出比边界更极端的值。"""
    rng = np.random.default_rng(4)
    z = 1760.0 + rng.normal(0, 3, (40, 40))
    z[15:25, 15:25] = np.nan
    ring = np.full(z.shape, False)
    ring[14:26, 14:26] = True
    ring[15:25, 15:25] = False
    lo, hi = np.nanmin(z[ring]), np.nanmax(z[ring])
    out = fill_holes(z)
    inner = out[15:25, 15:25]
    assert inner.min() >= lo - 1e-9 and inner.max() <= hi + 1e-9


def test_fill_holes_handles_multiple_components():
    xs = np.arange(40)[None, :] * np.ones((40, 1))
    truth = 1760.0 + 0.25 * xs
    z = truth.copy()
    z[5:9, 5:9] = np.nan
    z[28:33, 20:26] = np.nan
    out = fill_holes(z)
    assert np.isfinite(out).all()
    assert float(np.abs(out - truth).max()) < 1e-6


def test_fill_holes_returns_nan_when_nothing_valid():
    out = fill_holes(np.full((10, 10), np.nan))
    assert np.isnan(out).all()


def test_fill_holes_is_noop_without_holes():
    z = np.full((10, 10), 5.0)
    np.testing.assert_allclose(fill_holes(z), z)


def test_fill_holes_skips_large_nodata_sea():
    """测区外 nodata 与边缘缺口连成超大连通域时跳过，与全量 DSM 的 400k 上限一致。"""
    z = np.full((20, 20), np.nan)
    z[6:16, 6:16] = 1760.0
    out = fill_holes(z, max_component_cells=50)
    assert np.isnan(out[:6]).all()
    assert np.isfinite(out[8:14, 8:14]).all()


def test_fill_holes_fills_border_bay_inside_domain():
    """航带边缘单视区：商业正射有覆盖，DSM 缺口连着画幅外。必须在足迹域内补上。"""
    z = np.full((20, 20), np.nan)
    z[6:16, 6:16] = 1760.0
    domain = np.zeros((20, 20), bool)
    domain[2:16, 6:16] = True  # 足迹比立体匹配更靠北
    z[6:16, 6:16] = 1760.0 + np.arange(10)[None, :] * 0.0
    out = fill_holes(z, domain=domain)
    assert np.isfinite(out[2:16, 6:16]).all()
    assert np.isnan(out[0, 0])
    assert float(np.abs(out[2:6, 8:14] - 1760.0).max()) < 1e-5


def test_fill_holes_many_small_components_finishes_quickly():
    """全测区 DSM 会有上万个小洞。每个洞扫一遍整幅会把全量跑卡死。"""
    import time

    xs = np.arange(250, dtype=float)[None, :] * np.ones((180, 1))
    z = 1760.0 + 0.2 * xs
    rng = np.random.default_rng(0)
    z[rng.random(z.shape) < 0.03] = np.nan
    t0 = time.perf_counter()
    out = fill_holes(z)
    assert time.perf_counter() - t0 < 4.0
    assert np.isfinite(out).mean() > 0.95


def test_resample_height_preserves_linear_slope_on_refined_grid():
    src = Grid.from_bounds((673000.0, 2620000.0, 673008.0, 2620004.0), 1.0, CRS)
    xs, ys = src.cell_centers()
    z = (1760.0 + 0.4 * (xs - 673000.0)).astype(np.float32)
    dst = src.refine(0.5)
    got = resample_height(z, src, dst)
    tx, _ = dst.cell_centers()
    truth = 1760.0 + 0.4 * (tx - 673000.0)
    inner = slice(2, -2)
    assert got.shape == dst.shape
    assert float(np.abs(got[inner, inner] - truth[inner, inner]).max()) < 0.05


def test_resample_height_keeps_nodata():
    src = Grid.from_bounds((0.0, 0.0, 8.0, 8.0), 1.0, CRS)
    z = np.full(src.shape, 10.0, np.float32)
    z[:, :2] = np.nan
    dst = src.refine(0.5)
    got = resample_height(z, src, dst)
    assert np.isnan(got[:, :3]).all()
    assert np.isfinite(got[:, 6:]).all()
    # 贴边权重不满 1 时也不能把高程往 0 拉，否则边缘真正射会起竖条
    vals = got[:, 3:]
    vals = vals[np.isfinite(vals)]
    assert vals.size
    assert float(np.max(np.abs(vals - 10.0))) < 0.05


def _field(grid, z, conf=None):
    conf = np.where(np.isfinite(z), 0.5, np.nan) if conf is None else conf
    return HeightField(grid, z.astype(np.float32), conf.astype(np.float32),
                       np.full(grid.shape, 4, np.uint8))


def test_remove_spikes_keeps_clustered_pit_interior():
    """成片低坑的局部中值也是错的，邻域去尖放不过 —— 这是旧 DSM 发白的根因。"""
    z = np.full((80, 80), 1760.0)
    z[8:40, 8:40] = 700.0
    out = remove_spikes(z)
    assert np.isfinite(out[20, 20])
    assert out[20, 20] == pytest.approx(700.0)


def test_clip_z_kills_clustered_pits():
    z = np.full((80, 80), 1760.0)
    z[8:40, 8:40] = 700.0
    out = clip_z_to_plausible(z)
    assert np.isnan(out[20, 20])
    assert np.isfinite(out[60, 60])
    assert out[60, 60] == pytest.approx(1760.0)


def test_clip_z_respects_margin_params():
    """余量必须可配：窄余量会裁掉 12 m 凸起，宽余量（本测区 50 m）保留。"""
    z = np.full((20, 20), 1760.0)
    z[5, 5] = 1772.0
    ref = np.full(200, 1760.0)
    wide = clip_z_to_plausible(z, ref_z=ref, margin_lo_m=30.0, margin_hi_m=50.0)
    assert np.isfinite(wide[5, 5])
    tight = clip_z_to_plausible(z, ref_z=ref, margin_lo_m=5.0, margin_hi_m=5.0)
    assert np.isnan(tight[5, 5])


def test_clip_z_keeps_commercial_relief():
    rng = np.random.default_rng(0)
    z = np.clip(1721.0 + rng.normal(0, 42, (120, 120)), 1635.5, 1830.2)
    out = clip_z_to_plausible(z)
    assert np.isfinite(out).mean() > 0.99
    assert float(np.nanmin(out)) >= 1630.0
    assert float(np.nanmax(out)) <= 1840.0


def test_plausible_limits_ignore_sparse_flyers():
    ref = np.concatenate([np.full(2000, 1708.0), np.array([-101402.0, 1872.0])])
    rng = np.random.default_rng(2)
    ref[:2000] += rng.normal(0, 25, 2000)
    lo, hi = plausible_z_limits(ref)
    assert 1600.0 < lo < 1680.0
    assert 1780.0 < hi < 1900.0


def test_clip_z_sparse_envelope_drops_inband_pit():
    rng = np.random.default_rng(1)
    z = 1720.0 + rng.normal(0, 40, (120, 120))
    z[10:40, 10:40] = 1600.0
    sparse = np.clip(1720.0 + rng.normal(0, 25, 8000), 1656.0, 1798.0)
    out = clip_z_to_plausible(z, ref_z=sparse)
    assert np.isnan(out[20, 20])
    assert np.isfinite(out[80, 80])


def test_build_dsm_drops_range_outliers_and_fills_from_terrain():
    grid = _grid(60, gsd=1.0)
    z = np.full(grid.shape, 1760.0, np.float32)
    z[18:38, 18:38] = 700.0
    dsm = build_dsm(_field(grid, z), fill=True, ref_z=np.full(200, 1760.0))
    assert dsm.stats["dropped_range"] >= 100
    assert np.isfinite(dsm.z[28, 28])
    assert abs(float(dsm.z[28, 28]) - 1760.0) < 5.0


def test_complete_dsm_coverage_clips_pits_before_fill():
    grid = _grid(80, gsd=1.0)
    z = np.full(grid.shape, np.nan)
    z[20:70, 15:65] = 1760.0
    z[25:45, 20:40] = 700.0
    coverage = np.zeros(grid.shape, bool)
    coverage[20:70, 15:65] = True
    out = complete_dsm_coverage(z, grid, coverage, ref_z=np.full(200, 1760.0))
    assert np.isnan(out[0, 0])
    assert np.isfinite(out[30, 30])
    assert abs(float(out[30, 30]) - 1760.0) < 5.0


def test_build_dsm_drops_low_confidence_and_spikes():
    grid = _grid(40)
    z = np.full(grid.shape, 1760.0, np.float32)
    conf = np.full(grid.shape, 0.5, np.float32)
    z[5, 5] = 1785.0          # 局部粗差，仍在地形带内，交给去尖
    conf[20, 20] = 0.001      # 低置信
    dsm = build_dsm(_field(grid, z, conf), fill=False)
    assert np.isnan(dsm.z[5, 5])
    assert np.isnan(dsm.z[20, 20])
    assert dsm.stats["dropped_spikes"] >= 1
    assert dsm.stats["dropped_low_confidence"] >= 1


def test_build_dsm_keeps_only_main_component():
    grid = _grid(50)
    z = np.full(grid.shape, np.nan, np.float32)
    z[5:40, 5:40] = 1760.0
    z[47:50, 0:3] = 1762.0  # 远离主体的游离小块，对应正射上的绿斑
    dsm = build_dsm(_field(grid, z), fill=False, tolerance_m=50.0)
    assert np.isfinite(dsm.z[20, 20])
    assert np.isnan(dsm.z[48, 1])


def test_build_dsm_fills_and_reports_stats():
    grid = _grid(40)
    xs = np.arange(grid.width)[None, :] * np.ones((grid.height, 1))
    z = (1760.0 + 0.2 * xs).astype(np.float32)
    z[10:14, 10:14] = np.nan
    dsm = build_dsm(_field(grid, z))
    assert dsm.stats["fill_ratio"] == pytest.approx(1.0)
    assert dsm.stats["z_min"] < dsm.stats["z_median"] < dsm.stats["z_max"]
    assert dsm.stats["gsd"] == pytest.approx(grid.gsd)


def test_build_dsm_extends_into_nearby_footprint_bay():
    """立体匹配在航带北缘失败时，沿足迹把邻近缺口补上，正射才不会露出白边。"""
    grid = _grid(80, gsd=1.0)
    z = np.full(grid.shape, np.nan, np.float32)
    z[20:70, 15:65] = 1760.0
    coverage = np.zeros(grid.shape, bool)
    coverage[8:70, 15:65] = True  # 足迹比 DSM 主体更靠北 12 m
    dsm = build_dsm(_field(grid, z), coverage=coverage, tolerance_m=50.0)
    assert np.isfinite(dsm.z[10:20, 30:50]).all()
    assert np.isnan(dsm.z[0, 0])


def test_build_dsm_does_not_bridge_far_island_via_coverage():
    """远离主体的斑块（商业 alpha=0 的绿斑）即使落在足迹里也不能补回去。"""
    grid = _grid(80, gsd=1.0)
    z = np.full(grid.shape, np.nan, np.float32)
    z[5:40, 5:40] = 1760.0
    z[75:80, 75:80] = 1762.0  # 距主体约 50 m，超过补洞半径
    coverage = np.ones(grid.shape, bool)
    dsm = build_dsm(_field(grid, z), coverage=coverage, tolerance_m=50.0)
    assert np.isfinite(dsm.z[20, 20])
    assert np.isnan(dsm.z[77, 77])


def test_build_dsm_locks_silhouette_to_reference_coverage():
    """全量对标商业时：商业 DSM 有效范围是覆盖真值，缺口全补、多出来的突出裁掉。"""
    grid = _grid(80, gsd=1.0)
    z = np.full(grid.shape, np.nan, np.float32)
    z[55:75, 15:65] = 1760.0
    z[0:4, 0:4] = 1775.0  # 商业没有的游离角
    coverage = np.zeros(grid.shape, bool)
    coverage[5:75, 15:65] = True  # 比立体匹配更靠北 ~50 m
    dsm = build_dsm(
        _field(grid, z),
        coverage=coverage,
        tolerance_m=50.0,
        max_fill_gap_m=None,
        clip_to_coverage=True,
    )
    assert np.isfinite(dsm.z[10:20, 30:50]).all()
    assert np.isnan(dsm.z[2, 2])
    assert np.isnan(dsm.z[0, 40])
    assert np.isfinite(dsm.z).sum() == int(coverage.sum())


def test_to_dtm_removes_building_but_keeps_terrain():
    grid = _grid(120, gsd=0.5)
    xs = np.arange(grid.width)[None, :] * np.ones((grid.height, 1))
    terrain = 1760.0 + 0.05 * xs
    z = terrain.copy()
    z[50:62, 50:62] += 15.0  # 6 m 见方的建筑
    dsm = build_dsm(_field(grid, z.astype(np.float32)), tolerance_m=20.0)
    dtm = to_dtm(dsm, opening_m=12.0)
    # 建筑处应回落到地形高度附近
    assert abs(float(dtm.z[56, 56]) - float(terrain[56, 56])) < 3.0
    # 远离建筑处地形不应被削掉
    assert abs(float(dtm.z[20, 100]) - float(terrain[20, 100])) < 2.0


def test_write_geotiff_matches_commercial_spec(tmp_path):
    import rasterio

    grid = _grid(20)
    z = np.full(grid.shape, 1760.0, np.float32)
    z[0, 0] = np.nan
    dsm = build_dsm(_field(grid, z), fill=False)
    out = write_geotiff(dsm, tmp_path / "DSM.tif")
    with rasterio.open(out) as src:
        assert src.count == 1
        assert src.dtypes[0] == "float32"
        assert src.nodata == pytest.approx(float(NODATA))
        assert src.compression.name.lower() == "lzw"
        assert int(src.profile.get("blockysize") or 1) == 1
        assert str(src.crs) == CRS
        assert src.transform.a == pytest.approx(grid.gsd)
        arr = src.read(1, masked=True)
    assert arr.mask[0, 0]
    assert not arr.mask[10, 10]


def test_raster_ref_reads_metadata(tmp_path):
    grid = _grid(20)
    dsm = build_dsm(_field(grid, np.full(grid.shape, 1760.0, np.float32)))
    p = write_geotiff(dsm, tmp_path / "a.tif")
    ref = RasterRef.open(p)
    assert (ref.width, ref.height) == (grid.width, grid.height)
    assert ref.crs == CRS and ref.count == 1


def _write(tmp_path, name, z, grid):
    return write_geotiff(build_dsm(_field(grid, z.astype(np.float32)), fill=False), tmp_path / name)


def test_flatten_edge_z_only_changes_border_band():
    """贴边低通只改距 nodata 80 m 内的格子，内部真正射高程保持不动。"""
    from ms_mosaic.dsm import flatten_edge_z

    z = np.full((200, 200), 1760.0)
    # 短波长起皱：真正射边缘油彩的来源。20 m 块平均必须把它压掉。
    z += 8.0 * np.sin(np.linspace(0, 80, 200))[None, :]
    z[:, :10] = np.nan
    out = flatten_edge_z(z, gsd=1.0, win_m=20.0, band_m=40.0)
    assert np.allclose(out[20:180, 80:180], z[20:180, 80:180], equal_nan=True)
    edge = np.isfinite(out) & (np.arange(200)[None, :] < 45)
    assert np.nanstd(out[edge]) < 0.35 * np.nanstd(z[np.isfinite(z) & (np.arange(200)[None, :] < 45)])


def test_large_gap_fill_follows_slope_instead_of_plateau():
    """超过 Laplace 上限的大缺口必须在粗格网上解调和面，不能被最近邻铺成平台。

    最近邻把整片缺口铺成最近有效格的常值。实测交付 DSM 里这类平台占 11.1%，
    高程偏差 −36.1 m、中误差 40.8 m，而真匹配格网只有 −1.3 m / 10.5 m。
    """
    from scipy.ndimage import maximum_filter, minimum_filter

    n = 400
    ramp = 1700.0 + 0.05 * np.arange(n)[None, :] * np.ones((n, 1))
    z = ramp.copy()
    z[:, 120:330] = np.nan  # 84000 格，远超下面给的 Laplace 上限
    domain = np.ones_like(z, bool)
    out = fill_holes(z, max_component_cells=4000, domain=domain)

    assert np.isfinite(out).all()
    filled = out[:, 150:300]
    flat = maximum_filter(out, 3) == minimum_filter(out, 3)
    assert flat[:, 150:300].mean() < 0.05, "缺口被铺成了常值平台"
    assert np.abs(filled - ramp[:, 150:300]).max() < 1.0, "填出的面没跟着坡度走"


def test_compare_dsm_identical_rasters_pass(tmp_path):
    grid = _grid(60, gsd=0.2)
    xs = np.arange(grid.width)[None, :] * np.ones((grid.height, 1))
    ys = np.arange(grid.height)[:, None] * np.ones((1, grid.width))
    z = 1760.0 + 0.3 * xs + 0.2 * ys
    a = _write(tmp_path, "ours.tif", z, grid)
    b = _write(tmp_path, "theirs.tif", z, grid)
    rep = compare_dsm(a, b)
    assert rep.ok, rep.to_text()
    by_name = {i.name: i for i in rep.items}
    assert by_name["DSM 相关系数"].ours == pytest.approx(1.0, abs=1e-6)
    assert by_name["DSM 高程偏差均值 (m)"].ours == pytest.approx(0.0, abs=1e-4)


def test_compare_dsm_detects_vertical_bias(tmp_path):
    grid = _grid(60, gsd=0.2)
    xs = np.arange(grid.width)[None, :] * np.ones((grid.height, 1))
    z = 1760.0 + 0.3 * xs
    a = _write(tmp_path, "ours.tif", z + 8.0, grid)
    b = _write(tmp_path, "theirs.tif", z, grid)
    rep = compare_dsm(a, b)
    by_name = {i.name: i for i in rep.items}
    assert by_name["DSM 高程偏差均值 (m)"].ours == pytest.approx(8.0, abs=0.05)
    assert by_name["DSM 高程偏差均值 (m)"].passed is False
    # 纯系统偏差不该影响相关系数
    assert by_name["DSM 相关系数"].ours == pytest.approx(1.0, abs=1e-6)
    assert not rep.ok


def test_compare_dsm_detects_uncorrelated_surface(tmp_path):
    """两套面形无关时相关系数必须掉下来。

    「我们的」面必须是空间相关的随机地形而不是白噪声：白噪声逐格偏离局部中值
    好几米，会被 remove_spikes 当粗差正确剔掉 99%，重叠格网不足就根本算不出
    相关系数 —— 那是合成输入不像地形，不是比对逻辑的问题。
    """
    from scipy.ndimage import gaussian_filter

    grid = _grid(60, gsd=0.2)
    rng = np.random.default_rng(0)
    xs = np.arange(grid.width)[None, :] * np.ones((grid.height, 1))
    # 幅度与相关长度要让局部高差远小于去尖容差（2 m），否则真地形也会被剔掉
    rough = gaussian_filter(rng.normal(0, 1, grid.shape), 10.0)
    rough *= 1.5 / rough.std()
    a = _write(tmp_path, "ours.tif", 1760.0 + rough, grid)
    b = _write(tmp_path, "theirs.tif", 1760.0 + 0.3 * xs, grid)
    rep = compare_dsm(a, b)
    by_name = {i.name: i for i in rep.items}
    assert by_name["DSM 重叠区覆盖率"].ours > 0.9, rep.to_text()
    assert abs(by_name["DSM 相关系数"].ours) < 0.5
    assert by_name["DSM 相关系数"].passed is False
    assert not rep.ok


def test_fit_plane_residual_recovers_known_tilt():
    shape = (80, 120)
    gsd = 0.25
    rows, cols = np.indices(shape)
    x = (cols - cols.mean()) * gsd
    y = (rows - rows.mean()) * gsd
    # 造一个「常数 4 m + 东向 12 mm/m + 北向 -7 mm/m」的基准差
    theirs = np.full(shape, 1760.0)
    ours = theirs + 4.0 + 0.012 * x - (-0.007) * y
    plane = fit_plane_residual(ours, theirs, np.ones(shape, bool), gsd)
    assert plane["bias"] == pytest.approx(4.0, abs=1e-6)
    assert plane["slope_e_mm_per_m"] == pytest.approx(12.0, abs=1e-6)
    assert plane["slope_n_mm_per_m"] == pytest.approx(-7.0, abs=1e-6)
    assert plane["rmse"] == pytest.approx(0.0, abs=1e-9)


def test_fit_plane_residual_keeps_real_shape_error():
    """去基准只该吃掉常数与倾斜，不该吃掉真实的面形差异。"""
    shape = (61, 61)
    rows, cols = np.indices(shape)
    theirs = np.full(shape, 1760.0, float)
    # 凸起必须严格居中，否则它本身就带进一点真实倾斜
    bump = 3.0 * np.exp(-((rows - rows.mean()) ** 2 + (cols - cols.mean()) ** 2) / 50.0)
    ours = theirs + 2.0 + bump
    plane = fit_plane_residual(ours, theirs, np.ones(shape, bool), 0.5)
    assert plane["rmse"] > 0.2
    assert abs(plane["slope_e_mm_per_m"]) < 1e-6


def test_compare_dsm_separates_datum_tilt_from_shape(tmp_path):
    """两幅面形完全相同、只差一个倾斜基准时，去基准后的中误差应当归零。"""
    grid = _grid(120, gsd=0.25)
    rng = np.random.default_rng(7)
    from scipy.ndimage import gaussian_filter

    shape_field = gaussian_filter(rng.random(grid.shape), 3.0) * 20.0
    rows, cols = np.indices(grid.shape)
    tilt = 0.010 * (cols - cols.mean()) * grid.gsd  # 10 mm/m 东向倾斜
    a = _write(tmp_path, "ours.tif", 1760.0 + shape_field + 5.0 + tilt, grid)
    b = _write(tmp_path, "theirs.tif", 1760.0 + shape_field, grid)
    rep = compare_dsm(a, b)
    by = {i.name: i for i in rep.items}
    assert by["DSM 基准高差 (m)"].ours == pytest.approx(5.0, abs=0.05)
    assert by["DSM 基准东向倾斜 (mm/m)"].ours == pytest.approx(10.0, abs=0.3)
    # 未去基准时中误差很大，去掉常数与倾斜后应当接近 0
    assert by["DSM 高程中误差 (m)"].ours > 4.0
    assert by["DSM 去基准后中误差 (m)"].ours < 0.05
    assert by["DSM 去基准后中误差 (m)"].passed is True


def test_comparison_report_text_includes_verdicts():
    rep = ComparisonReport()
    rep.add("甲", 1.0, 1.0, delta=0.0, tolerance=0.1)
    rep.add("乙", 5.0, 1.0, delta=4.0, tolerance=0.1)
    rep.add("丙", "x", "x")
    text = rep.to_text()
    assert "| 项目 | 自研 | 商业 | 差值 | 判定 | 说明 |" in text
    assert "通过" in text and "未达标" in text
    assert rep.ok is False


def test_geometric_shift_recovers_known_translation(tmp_path):
    """人为把影像平移已知距离，相位相关应当把它找回来。"""
    from scipy.ndimage import gaussian_filter

    grid = _grid(200, gsd=0.25)
    rng = np.random.default_rng(1)
    tex = gaussian_filter(rng.random(grid.shape), 2.0) * 50.0 + 1760.0
    shift_cells = 6
    moved = np.roll(tex, (0, shift_cells), axis=(0, 1))
    a = _write(tmp_path, "ours.tif", moved, grid)
    b = _write(tmp_path, "theirs.tif", tex, grid)
    dx, dy, _ = geometric_shift(a, b)
    assert dx == pytest.approx(shift_cells * grid.gsd, abs=grid.gsd)
    assert dy == pytest.approx(0.0, abs=grid.gsd)
