"""密集匹配测试。核心用一个「已知地形 + 已知相机」的合成场景，
检验平面扫描能不能把地形找回来。"""

import numpy as np
import pytest

from ms_mosaic.camera import Camera, Pose, project
from ms_mosaic.dense import (
    DenseConfig,
    ImageCache,
    MIN_REFINE_Z_MARGIN_M,
    _sample_bilinear,
    _sgm_aggregate,
    _subpixel_z,
    _windowed_ncc,
    antialias_sigma,
    off_nadir_deg,
    prior_surface,
    refine_z_margin,
    search_bound_hits,
    sweep_tile,
)
from ms_mosaic.grid import (
    Grid,
    estimate_dsm_gsd,
    estimate_native_gsd,
    estimate_z_margin_m,
    grid_from_footprints,
    grid_from_points,
)

CRS = "EPSG:32647"
FLIGHT_Z = 1871.5


def test_grid_from_raster_keeps_commercial_origin(tmp_path):
    """商业原点不是 GSD 整数倍，from_raster 必须原样保留，否则像元对不齐。"""
    import rasterio
    from rasterio.transform import Affine

    from ms_mosaic.grid import Grid

    gsd = 0.053873647004366
    origin = (673906.6065905083, 2620549.445925035)
    t = Affine(gsd, 0.0, origin[0], 0.0, -gsd, origin[1])
    path = tmp_path / "ref.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=20, height=10, count=1, dtype="uint8",
        crs="EPSG:32647", transform=t,
    ) as ds:
        ds.write(np.zeros((10, 20), np.uint8), 1)
    g = Grid.from_raster(path)
    assert g.width == 20 and g.height == 10
    assert g.transform.c == pytest.approx(origin[0], abs=1e-9)
    assert g.transform.f == pytest.approx(origin[1], abs=1e-9)
    snapped = Grid.from_bounds(g.bounds, gsd, CRS)
    assert snapped.transform.c != pytest.approx(origin[0], abs=1e-6)


def test_commercial_product_dir_finds_sibling_but_is_opt_in(tmp_path):
    from ms_mosaic.pipeline import commercial_product_dir, resolve_benchmark_dir

    inp = tmp_path / "MAX_20251017_001"
    inp.mkdir()
    prod = tmp_path / "拼图结果"
    prod.mkdir()
    (prod / "DSM.tif").write_bytes(b"x")
    (prod / "Orthomosaic_pix_surf_group0.tif").write_bytes(b"x")
    assert commercial_product_dir(inp) == prod.resolve()
    assert resolve_benchmark_dir(None) is None
    with pytest.raises(ValueError, match="benchmark-dir"):
        resolve_benchmark_dir(tmp_path / "nope")


def test_estimate_dsm_gsd_from_height_and_focal():
    cam = Camera(key="Color", width=2048, height=1536, f=2100.0, cx=1024.0, cy=768.0)
    poses = {
        0: Pose.from_ypr(np.array([0.0, 0.0, 1871.5]), 0.0, -90.0, 0.0),
        1: Pose.from_ypr(np.array([10.0, 0.0, 1871.5]), 0.0, -90.0, 0.0),
    }
    ground_z = 1871.5 - 111.8
    native = estimate_native_gsd(cam, poses, ground_z)
    assert native == pytest.approx(111.8 / 2100.0, rel=1e-6)
    assert estimate_dsm_gsd(cam, poses, ground_z) == pytest.approx(2.0 * native, rel=1e-9)


def test_estimate_z_margin_scales_with_relief():
    flat = np.column_stack([np.zeros(40), np.zeros(40), np.full(40, 100.0)])
    assert estimate_z_margin_m(flat) == 8.0
    hilly = np.column_stack([np.zeros(80), np.zeros(80), np.linspace(0.0, 160.0, 80)])
    m = estimate_z_margin_m(hilly)
    assert 15.0 <= m <= 48.0


def test_compare_ortho_same_grid_counts_extra_and_missing(tmp_path):
    import rasterio
    from rasterio.transform import Affine

    from ms_mosaic.compare import compare_ortho
    from ms_mosaic.grid import Grid
    from ms_mosaic.products import RasterWriter, rgb_with_alpha

    gsd = 0.1
    grid = Grid(Affine(gsd, 0.0, 100.0, 0.0, -gsd, 200.0), 8, 8, CRS)
    ours = np.zeros((3, 8, 8), np.uint8)
    theirs = np.zeros((3, 8, 8), np.uint8)
    ours[:, 1:7, 1:7] = 40
    theirs[:, 1:7, 2:8] = 80
    vo = np.zeros((8, 8), bool)
    vt = np.zeros((8, 8), bool)
    vo[1:7, 1:7] = True
    vt[1:7, 2:8] = True
    op = tmp_path / "ours.tif"
    tp = tmp_path / "theirs.tif"
    with RasterWriter(op, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, 8, 8), rgb_with_alpha(ours, vo))
    with RasterWriter(tp, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, 8, 8), rgb_with_alpha(theirs, vt))
    rep = compare_ortho(op, tp)
    by = {i.name: i for i in rep.items}
    assert by["正射宽度"].delta == 0
    assert by["正射多余像元"].ours == 6
    assert by["正射缺失像元"].ours == 6


def test_geometric_shift_accepts_uint8_rgba(tmp_path):
    """商业 RGB 是 uint8，masked.filled(nan) 会直接炸。"""
    from rasterio.transform import Affine

    from ms_mosaic.compare import geometric_shift
    from ms_mosaic.grid import Grid
    from ms_mosaic.products import RasterWriter, rgb_with_alpha

    grid = Grid(Affine(0.1, 0.0, 100.0, 0.0, -0.1, 200.0), 40, 40, CRS)
    rgb = np.full((3, 40, 40), 40, np.uint8)
    valid = np.ones((40, 40), bool)
    valid[:2] = False
    op = tmp_path / "ours.tif"
    tp = tmp_path / "theirs.tif"
    with RasterWriter(op, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, 40, 40), rgb_with_alpha(rgb, valid))
    with RasterWriter(tp, grid, count=4, dtype="uint8", alpha=True) as w:
        w.write((0, 0, 40, 40), rgb_with_alpha(rgb, valid))
    dx, dy, peak = geometric_shift(op, tp)
    assert np.isfinite(peak)


def test_compare_geotiff_profile_flags_tile_mismatch(tmp_path):
    import rasterio
    from rasterio.transform import Affine

    from ms_mosaic.compare import ComparisonReport, compare_geotiff_profile

    t = Affine(0.1, 0.0, 0.0, 0.0, -0.1, 10.0)
    strip = tmp_path / "strip.tif"
    tiled = tmp_path / "tiled.tif"
    data = np.zeros((16, 16), np.uint8)
    kw = dict(driver="GTiff", width=16, height=16, count=1, dtype="uint8", crs="EPSG:32647", transform=t, compress="lzw")
    with rasterio.open(strip, "w", tiled=False, blockxsize=16, blockysize=1, **kw) as ds:
        ds.write(data, 1)
    with rasterio.open(tiled, "w", tiled=True, blockxsize=16, blockysize=16, **kw) as ds:
        ds.write(data, 1)
    rep = ComparisonReport()
    compare_geotiff_profile(strip, tiled, "T", rep)
    by = {i.name: i for i in rep.items}
    assert by["T tiled"].passed is False


def test_grid_from_bounds_snaps_and_covers():
    g = Grid.from_bounds((673906.61, 2619907.65, 674616.61, 2620549.45), 0.1, CRS)
    left, bottom, right, top = g.bounds
    assert left <= 673906.61 and bottom <= 2619907.65
    assert right >= 674616.61 and top >= 2620549.45
    assert g.gsd == pytest.approx(0.1)


def test_grid_refine_halves_gsd_and_keeps_bounds():
    g = Grid.from_bounds((0.0, 0.0, 100.0, 50.0), 0.2, CRS)
    r = g.refine(0.5)
    assert r.gsd == pytest.approx(0.1)
    assert r.width == g.width * 2 and r.height == g.height * 2
    np.testing.assert_allclose(r.bounds, g.bounds, atol=1e-9)


def test_grid_refine_coarsens_for_hierarchical_dsm():
    g = Grid.from_bounds((0.0, 0.0, 40.0, 40.0), 0.1, CRS)
    c = g.refine(4.0)
    assert c.gsd == pytest.approx(0.4)
    assert c.width == g.width // 4 and c.height == g.height // 4
    np.testing.assert_allclose(c.bounds, g.bounds, atol=1e-9)


def test_grid_cell_centers_match_transform():
    g = Grid.from_bounds((0.0, 0.0, 10.0, 10.0), 1.0, CRS)
    xs, ys = g.cell_centers()
    assert xs.shape == g.shape
    assert xs[0, 0] == pytest.approx(0.5)
    assert ys[0, 0] == pytest.approx(9.5)  # 第一行在北端


def test_grid_tiles_cover_everything():
    g = Grid.from_bounds((0.0, 0.0, 10.0, 6.0), 1.0, CRS)
    seen = np.zeros(g.shape, bool)
    for r0, c0, rows, cols in g.tiles(4, overlap=1):
        seen[r0 : r0 + rows, c0 : c0 + cols] = True
    assert seen.all()


def test_sample_bilinear_exact_on_integer_grid():
    img = np.arange(20, dtype=np.float32).reshape(4, 5)
    got = _sample_bilinear(img, np.array([2.0, 0.0]), np.array([1.0, 3.0]))
    np.testing.assert_allclose(got, [img[1, 2], img[3, 0]])


def test_sample_bilinear_interpolates_midpoint():
    img = np.array([[0.0, 10.0], [20.0, 30.0]], np.float32)
    got = _sample_bilinear(img, np.array([0.5]), np.array([0.5]))
    assert got[0] == pytest.approx(15.0)


def test_sample_bilinear_returns_nan_outside():
    img = np.zeros((4, 4), np.float32)
    got = _sample_bilinear(img, np.array([-1.0, 10.0]), np.array([1.0, 1.0]))
    assert np.isnan(got).all()


def test_windowed_ncc_is_one_for_identical_patches():
    rng = np.random.default_rng(0)
    a = rng.normal(100, 30, (40, 40)).astype(np.float32)
    ncc = _windowed_ncc(a, a, 7)
    inner = ncc[10:-10, 10:-10]
    np.testing.assert_allclose(inner, 1.0, atol=1e-3)


def test_windowed_ncc_is_invariant_to_gain_and_offset():
    rng = np.random.default_rng(1)
    a = rng.normal(100, 30, (40, 40)).astype(np.float32)
    ncc = _windowed_ncc(a, a * 1.7 + 25.0, 7)
    np.testing.assert_allclose(ncc[10:-10, 10:-10], 1.0, atol=1e-3)


def test_windowed_ncc_is_low_for_independent_noise():
    rng = np.random.default_rng(2)
    a = rng.normal(100, 30, (60, 60)).astype(np.float32)
    b = rng.normal(100, 30, (60, 60)).astype(np.float32)
    ncc = _windowed_ncc(a, b, 7)
    assert abs(float(np.nanmedian(ncc[10:-10, 10:-10]))) < 0.3


def test_windowed_ncc_nan_in_flat_region():
    flat = np.full((30, 30), 50.0, np.float32)
    assert np.isnan(_windowed_ncc(flat, flat, 7)[15, 15])


def test_sgm_aggregate_preserves_single_minimum():
    cost = np.ones((6, 8, 8), np.float32)
    cost[3] = 0.0
    agg = _sgm_aggregate(cost, 0.06, 0.35)
    assert int(np.argmin(agg[:, 4, 4])) == 3


def test_sgm_aggregate_fixes_weak_isolated_outlier():
    """单点代价被噪声带偏、邻域一致时，SGM 应当把它拉回邻域的层。"""
    cost = np.ones((8, 12, 12), np.float32)
    cost[5] = 0.1  # 全场真值层
    cost[1, 6, 6] = 0.0  # 单点异常，比该点的真值层更低
    cost[5, 6, 6] = 0.25  # 异常优势 0.25，小于跳层惩罚 p2
    assert int(np.argmin(cost[:, 6, 6])) == 1
    agg = _sgm_aggregate(cost, 0.06, 0.35)
    assert int(np.argmin(agg[:, 6, 6])) == 5


def test_sgm_aggregate_respects_strong_local_evidence():
    """反过来，若该点的证据足够强（优势超过跳层惩罚），SGM 不应把它抹平 ——
    否则屋顶、陡坎这类真实高程跳变会被削掉。"""
    cost = np.ones((8, 12, 12), np.float32)
    cost[5] = 0.1
    cost[1, 6, 6] = 0.0
    cost[5, 6, 6] = 0.9  # 优势 0.9，远大于 p2
    agg = _sgm_aggregate(cost, 0.06, 0.35)
    assert int(np.argmin(agg[:, 6, 6])) == 1


def test_subpixel_z_finds_parabola_vertex():
    z_values = np.linspace(-1.0, 1.0, 5)
    # 顶点刻意放在第 2、3 层之间
    cost = np.empty((5, 1, 1), np.float32)
    for i, z in enumerate(z_values):
        cost[i, 0, 0] = (z - 0.25) ** 2
    best = np.array([[3]])
    got = _subpixel_z(cost, best, z_values)
    assert got[0, 0] == pytest.approx(0.25, abs=0.02)


def test_off_nadir_angle():
    assert off_nadir_deg(Pose.from_ypr(np.zeros(3), 0.0, -90.0, 0.0)) == pytest.approx(0.0)
    assert off_nadir_deg(Pose.from_ypr(np.zeros(3), 30.0, -75.0, 0.0)) == pytest.approx(15.0)


def test_prior_surface_reproduces_smooth_terrain():
    rng = np.random.default_rng(3)
    xs = 673000.0 + rng.random(4000) * 60.0
    ys = 2620000.0 + rng.random(4000) * 60.0
    zs = 1760.0 + 0.4 * (xs - 673000.0)
    pts = np.stack([xs, ys, zs], axis=1)
    grid = Grid.from_bounds((673000.0, 2620000.0, 673060.0, 2620060.0), 1.0, CRS)
    prior = prior_surface(pts, grid, smooth_cells=2.0)
    assert np.isfinite(prior).all()
    gx, gy = grid.cell_centers()
    truth = 1760.0 + 0.4 * (gx - 673000.0)
    inner = slice(8, -8)
    assert float(np.abs(prior[inner, inner] - truth[inner, inner]).max()) < 1.5


def test_prior_surface_rejects_points_outside_grid():
    pts = np.array([[0.0, 0.0, 10.0]])
    grid = Grid.from_bounds((673000.0, 2620000.0, 673010.0, 2620010.0), 1.0, CRS)
    with pytest.raises(ValueError, match="格网之外"):
        prior_surface(pts, grid)


class _MemoryCache(ImageCache):
    """直接喂内存里的影像，测试不落盘。金字塔与预平滑仍走真实实现。"""

    def __init__(self, arrays, **kwargs):
        super().__init__({i: None for i in arrays}, **kwargs)
        self._arrays = arrays

    def _read(self, index):
        return np.asarray(self._arrays[index], np.float32)


def _synthetic_survey(relief: float = 18.0, texture_seed: int = 7):
    """造一片有起伏的地形和一组正下视影像，影像内容由地形上的纹理正投而来。"""
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(texture_seed)
    # 地面纹理：定义在一个比测区更大的平面上，用平滑噪声保证 SIFT/NCC 可用
    tex_gsd = 0.25
    tex_n = 900
    tex_origin = np.array([672950.0, 2619950.0])
    texture = gaussian_filter(rng.random((tex_n, tex_n)).astype(np.float32), 1.6)
    texture = (texture - texture.min()) / (texture.max() - texture.min()) * 255.0

    def terrain(x, y):
        return (
            1760.0
            + relief * np.sin((x - 673000.0) / 18.0)
            + 0.5 * relief * np.cos((y - 2620000.0) / 25.0)
        )

    cam = Camera.initial("Color", 512, 512, kind="rgb")
    cam = cam.with_vector(np.array([560.0, 256.0, 256.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]))

    poses, images = {}, {}
    uu, vv = np.meshgrid(np.arange(cam.width), np.arange(cam.height))
    for idx, (dx, dy) in enumerate([(0.0, 0.0), (14.0, 0.0), (0.0, 14.0), (14.0, 14.0), (7.0, -12.0)]):
        center = np.array([673030.0 + dx, 2620030.0 + dy, FLIGHT_Z])
        pose = Pose.from_ypr(center, 0.0, -90.0, 0.0)
        poses[idx] = pose
        # 对每个像素求视线与地形的交点（迭代几次即可收敛，地形平缓）
        from ms_mosaic.camera import ray_directions

        dirs = ray_directions(cam, pose, uu.ravel().astype(float), vv.ravel().astype(float))
        z_guess = np.full(dirs.shape[0], 1760.0)
        for _ in range(12):
            t = (z_guess - center[2]) / dirs[:, 2]
            x = center[0] + dirs[:, 0] * t
            y = center[1] + dirs[:, 1] * t
            z_guess = terrain(x, y)
        t = (z_guess - center[2]) / dirs[:, 2]
        x = center[0] + dirs[:, 0] * t
        y = center[1] + dirs[:, 1] * t
        tc = np.clip(((x - tex_origin[0]) / tex_gsd), 0, tex_n - 1.001)
        tr = np.clip(((y - tex_origin[1]) / tex_gsd), 0, tex_n - 1.001)
        images[idx] = _sample_bilinear(texture, tc, tr).reshape(cam.height, cam.width).astype(
            np.float32
        )
    cameras = {i: cam for i in poses}
    return cameras, poses, images, terrain


@pytest.mark.slow
def test_sweep_tile_recovers_synthetic_terrain():
    cameras, poses, images, terrain = _synthetic_survey(relief=18.0)
    grid = Grid.from_bounds((673020.0, 2620020.0, 673044.0, 2620044.0), 0.4, CRS)
    gx, gy = grid.cell_centers()
    truth = terrain(gx, gy)
    # 先验面刻意给成整体偏移 + 抹平起伏，逼扫描自己找回真高程
    prior = np.full(grid.shape, float(np.mean(truth)), np.float32)

    cfg = DenseConfig(window=7, n_layers=56, z_margin_m=22.0, max_views=5, top_k_views=3, min_ncc=0.2)
    z, conf, views = sweep_tile(
        grid, (0, 0, grid.height, grid.width), prior, cameras, poses, _MemoryCache(images), cfg
    )

    inner = slice(8, -8)
    got = z[inner, inner]
    ref = truth[inner, inner]
    ok = np.isfinite(got)
    assert ok.mean() > 0.8, f"解出比例仅 {ok.mean():.2f}"
    err = np.abs(got[ok] - ref[ok])
    # 起伏 27 m 的地形，先验被抹平成常数，中位误差应当远小于起伏本身
    assert float(np.median(err)) < 1.0, f"高程中位误差 {np.median(err):.2f} m"
    assert float(np.percentile(err, 90)) < 3.0
    assert views[inner, inner].max() >= 2


@pytest.mark.slow
def test_sweep_tile_gives_up_without_enough_views():
    cameras, poses, images, terrain = _synthetic_survey()
    only_one = {0: poses[0]}
    grid = Grid.from_bounds((673020.0, 2620020.0, 673030.0, 2620030.0), 0.5, CRS)
    prior = np.full(grid.shape, 1760.0, np.float32)
    z, _, views = sweep_tile(
        grid, (0, 0, grid.height, grid.width), prior,
        {0: cameras[0]}, only_one, _MemoryCache(images), DenseConfig(),
    )
    assert np.isnan(z).all()
    assert views.max() == 0


def test_antialias_sigma_zero_when_grid_finer_than_image():
    assert antialias_sigma(0.05, 0.054) == 0.0
    assert antialias_sigma(0.054, 0.054) == 0.0


def test_antialias_sigma_matches_theory_in_mild_undersampling():
    # 真实配置：DSM 格网 0.1077 m / 影像 0.0484 m ≈ 2.2 倍
    s = antialias_sigma(0.107747293, 0.0484)
    assert s == pytest.approx(0.5 * np.sqrt((0.107747293 / 0.0484) ** 2 - 1), rel=1e-9)
    assert 0.8 < s < 1.2


def test_antialias_sigma_is_capped_for_severe_undersampling():
    """理论值会随欠采样倍数无上限增长，但过度平滑会磨掉真实纹理，必须设限。"""
    from ms_mosaic.dense import MAX_ANTIALIAS_SIGMA

    assert antialias_sigma(0.4, 0.054) == pytest.approx(MAX_ANTIALIAS_SIGMA)
    assert antialias_sigma(4.0, 0.054) == pytest.approx(MAX_ANTIALIAS_SIGMA)


def test_image_cache_applies_antialias_blur(tmp_path):
    """缓存要在采样前做预平滑，且同一影像只读一次。"""
    import tifffile

    rng = np.random.default_rng(5)
    raw = (rng.random((64, 64)) * 255).astype(np.uint8)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, raw)

    sharp = ImageCache({0: path}, sigma=0.0)[0]
    blurred = ImageCache({0: path}, sigma=3.0)[0]
    assert sharp.shape == blurred.shape == (64, 64)
    # 预平滑必须真正削掉高频：相邻像元差分的方差应显著下降
    assert np.var(np.diff(blurred, axis=1)) < 0.2 * np.var(np.diff(sharp, axis=1))

    cache = ImageCache({0: path}, sigma=1.0)
    first = cache[0]
    assert cache[0] is first  # 命中缓存，不重复读盘与平滑


def test_image_cache_evicts_beyond_limit(tmp_path):
    import tifffile

    paths = {}
    for i in range(4):
        p = tmp_path / f"{i}.tif"
        tifffile.imwrite(p, np.full((8, 8), i * 10, np.uint8))
        paths[i] = p
    cache = ImageCache(paths, limit=2)
    for i in range(4):
        cache[i]
    assert len(cache._cache) <= 2


@pytest.mark.slow
def test_view_count_dropouts_do_not_bias_height():
    """部分视图在部分高程层落到幅外时，代价不得被「有多少视图在幅内」左右。

    做法：只保留能看到测区一角的视图组合，让在幅内的视图数随假设高程明显变化，
    再检验解出的高程仍然落在真值附近。此前 top-k 均值把无效视图当 -1 凑数，
    这种场景下高程会被系统性拉偏数米。
    """
    cameras, poses, images, terrain = _synthetic_survey(relief=14.0)
    # 挑测区边缘的一块：部分视图只能看到它的一部分
    grid = Grid.from_bounds((673016.0, 2620016.0, 673032.0, 2620032.0), 0.4, CRS)
    gx, gy = grid.cell_centers()
    truth = terrain(gx, gy)
    prior = np.full(grid.shape, float(np.mean(truth)), np.float32)
    cfg = DenseConfig(window=7, n_layers=48, z_margin_m=20.0, max_views=5,
                      top_k_views=4, pyramid_levels=1, min_ncc=0.2)
    z, _, count = sweep_tile(
        grid, (0, 0, grid.height, grid.width), prior, cameras, poses,
        _MemoryCache(images), cfg,
    )
    inner = slice(8, -8)
    ok = np.isfinite(z[inner, inner])
    assert ok.mean() > 0.5
    d = z[inner, inner][ok] - truth[inner, inner][ok]
    # 关键是偏差（系统性拉偏），而不只是离散度
    assert abs(float(np.median(d))) < 1.0, f"高程系统偏差 {np.median(d):.2f} m"
    # 判无效的格网，视图数必须一并归零，否则下游会误以为有观测支撑
    assert (count[~np.isfinite(z)] == 0).all()


@pytest.mark.slow
def test_pyramid_beats_single_level_at_same_layer_budget():
    """相同层数预算下，由粗到细应当比单层扫描精度更高。"""
    cameras, poses, images, terrain = _synthetic_survey(relief=16.0)
    grid = Grid.from_bounds((673024.0, 2620024.0, 673040.0, 2620040.0), 0.4, CRS)
    gx, gy = grid.cell_centers()
    truth = terrain(gx, gy)
    prior = np.full(grid.shape, float(np.mean(truth)), np.float32)
    inner = slice(8, -8)
    cache = _MemoryCache(images)

    errs = {}
    for label, cfg in (
        ("single", DenseConfig(n_layers=48, z_margin_m=20.0, max_views=5, pyramid_levels=1)),
        ("pyramid", DenseConfig(n_layers=24, z_margin_m=20.0, max_views=5, pyramid_levels=2)),
    ):
        z, _, _ = sweep_tile(
            grid, (0, 0, grid.height, grid.width), prior, cameras, poses, cache, cfg
        )
        ok = np.isfinite(z[inner, inner])
        errs[label] = float(np.median(np.abs(z[inner, inner][ok] - truth[inner, inner][ok])))
    assert errs["pyramid"] < errs["single"], errs


def test_grid_from_points_uses_percentiles():
    pts = np.array([[0.0, 0.0, 0.0]] * 100 + [[1e6, 1e6, 0.0]])
    g = grid_from_points(np.array(pts), 1.0, CRS, percentile=2.0)
    # 单个飞点不应把格网拉到 100 万米宽
    assert g.width < 100


def test_grid_from_footprints_is_larger_than_point_percentile_box():
    from ms_mosaic.camera import Camera, Pose

    cam = Camera.initial("Color", 400, 300, kind="rgb")
    poses = {
        0: Pose.from_ypr(np.array([674200.0, 2620200.0, 1870.0]), 0.0, -90.0, 0.0),
        1: Pose.from_ypr(np.array([674260.0, 2620200.0, 1870.0]), 0.0, -90.0, 0.0),
    }
    cams = {0: cam, 1: cam}
    pts = np.array([[674200.0, 2620200.0, 1760.0], [674260.0, 2620200.0, 1760.0]])
    g_pts = grid_from_points(pts, 1.0, CRS, percentile=0.0)
    g_fp = grid_from_footprints(cams, poses, 1760.0, 1.0, CRS, pad_m=0.0)
    # 足迹比摄站连线更宽，格网不应再裁成一条细矩形
    assert g_fp.width * g_fp.height > g_pts.width * g_pts.height


def test_refine_z_margin_does_not_collapse_to_one_step():
    """±12 m / 48 层时旧公式细层只剩 ±1 m，必须抬到下限。"""
    old = 2.0 * (2.0 * 12.0 / 47)
    assert old == pytest.approx(24.0 / 47 * 2.0)
    got = refine_z_margin(12.0, 48)
    assert got == pytest.approx(MIN_REFINE_Z_MARGIN_M)
    assert got > old
    # 宽窗口粗层：细层取 max(4 m, 3 个粗步长)
    wide = refine_z_margin(24.0, 48)
    assert wide >= MIN_REFINE_Z_MARGIN_M
    assert wide == pytest.approx(max(4.0, 3.0 * (48.0 / 47)))


def test_search_bound_hits_marks_first_and_last_layer():
    best = np.array([[0, 1], [23, 47]])
    hits = search_bound_hits(best, 48)
    assert hits.tolist() == [[True, False], [False, True]]
