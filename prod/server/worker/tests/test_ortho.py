"""真正射与遮挡检测的测试。

几何正确性用闭环验证：把贴在地形上的已知纹理渲染成影像，再正射还原，
还原结果应当与原纹理在同一平面位置上对上。这能同时抓出投影、采样、
格网对齐三处的错误。
"""

import numpy as np
import pytest

from ms_mosaic.camera import Pose, project
from ms_mosaic.grid import Grid
from ms_mosaic.ortho import (
    NativeImageCache,
    OrthoConfig,
    best_view_mosaic,
    neighbor_drop,
    orthorectify_tile,
    overlap_count,
    select_views,
    view_score,
    visibility_zbuffer,
)
from synthetic import BASE_Z, FLIGHT_Z, NADIR_GRID, MemoryCache, render_survey

CRS = "EPSG:32647"


def test_view_score_prefers_nadir_over_oblique():
    survey = render_survey([(0.0, 0.0)])
    cam, pose = survey.cameras[0], survey.poses[0]
    pt = np.array([[673030.0, 2620030.0, BASE_Z]])
    nadir = view_score(cam, pose, pt, np.array([256.0]), np.array([256.0]))
    # 同一地面点，摄站平移到侧方后入射角变斜
    far = Pose.from_ypr(np.array([673030.0 + 90.0, 2620030.0, FLIGHT_Z]), 0.0, -90.0, 0.0)
    oblique = view_score(cam, far, pt, np.array([256.0]), np.array([256.0]))
    assert nadir[0] > oblique[0]


def test_view_score_prefers_image_center_over_edge():
    survey = render_survey([(0.0, 0.0)])
    cam, pose = survey.cameras[0], survey.poses[0]
    pt = np.array([[673030.0, 2620030.0, BASE_Z]])
    center = view_score(cam, pose, pt, np.array([256.0]), np.array([256.0]))
    edge = view_score(cam, pose, pt, np.array([8.0]), np.array([8.0]))
    assert center[0] > edge[0]


def test_view_score_is_zero_looking_up():
    survey = render_survey([(0.0, 0.0)])
    cam = survey.cameras[0]
    # 摄站在地面点之下：入射角余弦被截到 0
    below = Pose.from_ypr(np.array([673030.0, 2620030.0, BASE_Z - 50.0]), 0.0, -90.0, 0.0)
    pt = np.array([[673030.0, 2620030.0, BASE_Z]])
    assert view_score(cam, below, pt, np.array([256.0]), np.array([256.0]))[0] == 0.0


def test_visibility_zbuffer_all_visible_on_flat_ground():
    survey = render_survey([(0.0, 0.0)], relief=0.0)
    cam, pose = survey.cameras[0], survey.poses[0]
    xs, ys = np.meshgrid(
        np.linspace(673020.0, 673040.0, 40), np.linspace(2620020.0, 2620040.0, 40)
    )
    pts = np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, BASE_Z)], axis=1)
    u, v, ok = project(cam, pose, pts)
    inside = ok & cam.in_bounds(u, v, margin=4)
    seen = visibility_zbuffer(cam, pose, pts, u, v, inside, tolerance_m=0.6)
    # 平坦地面无遮挡，幅内的点应当全部可见
    assert seen[inside].all()
    assert not seen[~inside].any()


WALL = (673034.0, 673036.0, 2620010.0, 2620050.0, 25.0)


def _wall_scene():
    """一堵 25 m 高的南北向墙，摄站在其西侧 24 m，墙东侧地面落在阴影里。

    Z-buffer 必须用二维格网喂：一维剖面上，同一像素往往只有一个采样点，
    根本形不成竞争关系。
    """
    survey = render_survey([(0.0, 0.0)], relief=0.0, wall=WALL)
    pose = Pose.from_ypr(np.array([673010.0, 2620030.0, FLIGHT_Z]), 0.0, -90.0, 0.0)
    grid = Grid.from_bounds((673020.0, 2620020.0, 673050.0, 2620040.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    z = survey.terrain(gx, gy)
    pts = np.stack([gx.ravel(), gy.ravel(), z.ravel()], axis=1)
    return survey, pose, grid, gx, z, pts


def _shadow_geometry():
    """解析算出阴影范围，供断言使用。

    摄站 (673010, 1871.5)，地面 1760，墙顶 1785。掠过墙东顶角的视线延伸到
    地面的落点：Δx = 26 × 111.5 / 86.5 = 33.5 m，即 x = 673043.5。
    故阴影区为墙东侧 673036 ~ 673043.5。
    """
    return 673036.0, 673010.0 + 26.0 * 111.5 / 86.5


def test_visibility_zbuffer_hides_ground_behind_wall():
    survey, pose, grid, gx, z, pts = _wall_scene()
    cam = survey.cameras[0]
    u, v, ok = project(cam, pose, pts)
    inside = ok & cam.in_bounds(u, v, margin=2)
    drop = neighbor_drop(z).ravel()
    seen = visibility_zbuffer(cam, pose, pts, u, v, inside, tolerance_m=0.6, drop_m=drop, gsd=grid.gsd)

    xf = gx.ravel()
    shadow_lo, shadow_hi = _shadow_geometry()
    # 留 0.5 m 余量避开边界过渡带
    shadow = inside & (xf > shadow_lo + 0.5) & (xf < shadow_hi - 0.5)
    front = inside & (xf < WALL[0] - 0.5)
    assert front.sum() > 100 and shadow.sum() > 100
    assert seen[front].mean() > 0.95, "墙前的地面不该被遮挡"
    assert seen[shadow].mean() < 0.1, f"墙后阴影区仍有 {seen[shadow].mean():.1%} 判为可见"


def test_visibility_zbuffer_needs_facade_samples():
    """反证：不补立面采样时，墙后阴影会被大面积漏判。"""
    survey, pose, grid, gx, z, pts = _wall_scene()
    cam = survey.cameras[0]
    u, v, ok = project(cam, pose, pts)
    inside = ok & cam.in_bounds(u, v, margin=2)
    xf = gx.ravel()
    shadow_lo, shadow_hi = _shadow_geometry()
    shadow = inside & (xf > shadow_lo + 0.5) & (xf < shadow_hi - 0.5)

    without = visibility_zbuffer(cam, pose, pts, u, v, inside, tolerance_m=0.6)
    with_facade = visibility_zbuffer(
        cam, pose, pts, u, v, inside, tolerance_m=0.6, drop_m=neighbor_drop(z).ravel(), gsd=grid.gsd
    )
    assert without[shadow].mean() > 0.5, "本应漏判，若已很低说明这条反证失效"
    assert with_facade[shadow].mean() < without[shadow].mean() - 0.4


def test_visibility_zbuffer_keeps_wall_top_visible():
    survey, pose, grid, gx, z, pts = _wall_scene()
    cam = survey.cameras[0]
    u, v, ok = project(cam, pose, pts)
    inside = ok & cam.in_bounds(u, v, margin=2)
    seen = visibility_zbuffer(
        cam, pose, pts, u, v, inside, tolerance_m=0.6, drop_m=neighbor_drop(z).ravel(), gsd=grid.gsd
    )
    xf = gx.ravel()
    top = inside & (xf > WALL[0] + 0.3) & (xf < WALL[1] - 0.3)
    assert top.sum() > 50
    assert seen[top].mean() > 0.95, "墙顶自身必须可见"


def test_neighbor_drop_finds_wall_edge():
    survey, pose, grid, gx, z, pts = _wall_scene()
    drop = neighbor_drop(z)
    # 墙顶靠外一圈相对邻域低处有约 25 m 高差，平坦地面几乎为 0
    assert drop.max() == pytest.approx(25.0, abs=0.5)
    flat = np.abs(gx - 673025.0) < 1.0
    assert float(drop[flat].max()) < 0.1


def test_neighbor_drop_ignores_nan():
    z = np.full((10, 10), 100.0)
    z[5, 5] = np.nan
    drop = neighbor_drop(z)
    assert drop[5, 5] == 0.0
    assert np.isfinite(drop).all()


def test_visibility_zbuffer_empty_input():
    survey = render_survey([(0.0, 0.0)])
    cam, pose = survey.cameras[0], survey.poses[0]
    pts = np.zeros((5, 3))
    seen = visibility_zbuffer(
        cam, pose, pts, np.zeros(5), np.zeros(5), np.zeros(5, bool), tolerance_m=0.6
    )
    assert not seen.any()


def test_select_views_skips_high_tilt():
    survey = render_survey(NADIR_GRID)
    grid = Grid.from_bounds((673024.0, 2620024.0, 673036.0, 2620036.0), 0.2, CRS)
    poses = dict(survey.poses)
    # 加一个 80° 倾斜的摄站，应被 max_tilt_deg=60 挡掉
    poses[99] = Pose.from_ypr(np.array([673030.0, 2620030.0, FLIGHT_Z]), 0.0, -10.0, 0.0)
    cameras = {i: survey.cameras[0] for i in poses}
    got = select_views(grid, (0, 0, grid.height, grid.width), BASE_Z, cameras, poses,
                       OrthoConfig(max_tilt_deg=60.0))
    assert 99 not in got
    assert len(got) >= 2


def test_select_views_respects_max_views():
    survey = render_survey(NADIR_GRID)
    grid = Grid.from_bounds((673024.0, 2620024.0, 673036.0, 2620036.0), 0.2, CRS)
    got = select_views(grid, (0, 0, grid.height, grid.width), BASE_Z,
                       survey.cameras, survey.poses, OrthoConfig(max_views=2))
    assert len(got) == 2


@pytest.mark.slow
def test_orthorectify_recovers_ground_texture():
    """闭环验证：正射结果应当还原贴在地形上的原始纹理。"""
    survey = render_survey(NADIR_GRID, relief=16.0)
    grid = Grid.from_bounds((673024.0, 2620024.0, 673038.0, 2620038.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    z = survey.terrain(gx, gy).astype(np.float32)

    stack = orthorectify_tile(
        grid, (0, 0, grid.height, grid.width), z,
        survey.cameras, survey.poses, MemoryCache(survey.images),
    )
    mosaic, label = best_view_mosaic(stack)
    truth = survey.texture(gx, gy)[0]

    ok = np.isfinite(mosaic[0]) & np.isfinite(truth)
    assert ok.mean() > 0.9, f"正射有效率仅 {ok.mean():.2f}"
    # 真值与还原值应当高度相关；残差主要来自双线性重采样
    corr = float(np.corrcoef(mosaic[0][ok], truth[ok])[0, 1])
    assert corr > 0.98, f"相关系数仅 {corr:.4f}"
    rmse = float(np.sqrt(np.mean((mosaic[0][ok] - truth[ok]) ** 2)))
    assert rmse < 12.0, f"灰度中误差 {rmse:.2f}（量程 0~255）"
    assert set(np.unique(label[ok])) - {-1}


@pytest.mark.slow
def test_orthorectify_with_wrong_dsm_misplaces_texture():
    """反证：若高程面错了，正射就会错位 —— 说明上一条测的确实是 DSM 起作用。

    这正是早期版本「按单一水平面投影」导致拼图重叠错位的机理。错位量是
    高程误差 × tan(入射角)，所以必须挑一块斜视的区域来验：正对摄站的
    区域入射角接近 0，高程给错了也几乎不位移。
    """
    survey = render_survey(NADIR_GRID, relief=24.0)
    # 摄站群在 673030 附近，这块选在东侧 32 m 处，入射角约 16°
    grid = Grid.from_bounds((673056.0, 2620024.0, 673070.0, 2620038.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    truth = survey.texture(gx, gy)[0]
    cache = MemoryCache(survey.images)
    window = (0, 0, grid.height, grid.width)

    good = survey.terrain(gx, gy).astype(np.float32)
    flat = np.full(grid.shape, float(np.mean(good)), np.float32)

    corrs = []
    for z in (good, flat):
        mosaic, _ = best_view_mosaic(
            orthorectify_tile(grid, window, z, survey.cameras, survey.poses, cache)
        )
        ok = np.isfinite(mosaic[0]) & np.isfinite(truth)
        corrs.append(float(np.corrcoef(mosaic[0][ok], truth[ok])[0, 1]))
    assert corrs[0] > corrs[1] + 0.1, f"真实DSM {corrs[0]:.3f} vs 水平面 {corrs[1]:.3f}"


@pytest.mark.slow
def test_orthorectify_handles_multiband():
    survey = render_survey(NADIR_GRID[:3], relief=10.0, n_bands=3)
    grid = Grid.from_bounds((673026.0, 2620026.0, 673034.0, 2620034.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    z = survey.terrain(gx, gy).astype(np.float32)
    stack = orthorectify_tile(
        grid, (0, 0, grid.height, grid.width), z,
        survey.cameras, survey.poses, MemoryCache(survey.images),
    )
    assert stack.n_bands == 3
    mosaic, _ = best_view_mosaic(stack)
    assert mosaic.shape[0] == 3
    truth = survey.texture(gx, gy)
    for b in range(3):
        ok = np.isfinite(mosaic[b]) & np.isfinite(truth[b])
        assert float(np.corrcoef(mosaic[b][ok], truth[b][ok])[0, 1]) > 0.97


def test_orthorectify_returns_empty_without_elevation():
    survey = render_survey(NADIR_GRID[:2])
    grid = Grid.from_bounds((673026.0, 2620026.0, 673030.0, 2620030.0), 0.2, CRS)
    z = np.full(grid.shape, np.nan, np.float32)
    stack = orthorectify_tile(
        grid, (0, 0, grid.height, grid.width), z,
        survey.cameras, survey.poses, MemoryCache(survey.images),
    )
    assert stack.views == []
    mosaic, label = best_view_mosaic(stack)
    assert np.isnan(mosaic).all() and (label == -1).all()


@pytest.mark.slow
def test_overlap_count_matches_view_availability():
    survey = render_survey(NADIR_GRID, relief=8.0)
    grid = Grid.from_bounds((673026.0, 2620026.0, 673034.0, 2620034.0), 0.1, CRS)
    gx, gy = grid.cell_centers()
    z = survey.terrain(gx, gy).astype(np.float32)
    stack = orthorectify_tile(
        grid, (0, 0, grid.height, grid.width), z,
        survey.cameras, survey.poses, MemoryCache(survey.images),
    )
    n = overlap_count(stack)
    assert n.max() >= 2
    assert n.max() <= len(stack.views)
    # 重叠度与逐视角有效掩膜必须一致
    np.testing.assert_array_equal(n, np.isfinite(stack.scores).sum(axis=0).astype(np.uint8))


def test_native_image_cache_shapes_and_eviction(tmp_path):
    import tifffile
    from PIL import Image

    gray = tmp_path / "ms.tif"
    tifffile.imwrite(gray, (np.arange(64 * 64, dtype=np.uint16) % 4096).reshape(64, 64))
    rgb = tmp_path / "rgb.jpg"
    Image.fromarray(np.zeros((48, 32, 3), np.uint8)).save(rgb)

    cache = NativeImageCache({0: gray, 1: rgb}, limit=1)
    assert cache[0].shape == (1, 64, 64)  # 多光谱单波段
    assert cache[1].shape == (3, 48, 32)  # RGB 三波段，通道在前
    assert len(cache._cache) == 1  # 超出上限后淘汰
