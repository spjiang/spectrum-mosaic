"""质量报告数据组装：GPS 分箱、航线切割、连通序列。"""

from pathlib import Path

import numpy as np
from rasterio.transform import Affine

from ms_mosaic.grid import Grid
from ms_mosaic.qa import (
    connected_sequences,
    crs_label,
    format_gps_extrema_line,
    gps_extrema,
    gps_histogram,
    mean_flight_agl_m,
    split_strips,
    survey_area_km2,
    truncate_rows,
)


def test_survey_area_uses_valid_bbox_not_full_grid():
    # 100×100 格网、GSD=1 m → 整幅 0.01 km²；有效区仅左上 50×40
    gsd = 1.0
    grid = Grid(Affine(gsd, 0.0, 0.0, 0.0, -gsd, 100.0), 100, 100, "EPSG:32650")
    z = np.full((100, 100), np.nan, np.float32)
    z[:40, :50] = 10.0
    area = survey_area_km2(z, grid)
    assert abs(area - (50 * 40) / 1e6) < 1e-12
    # 全空时退回整幅外框
    empty = np.full((100, 100), np.nan, np.float32)
    assert abs(survey_area_km2(empty, grid) - 0.01) < 1e-12


def test_mean_flight_agl_prefers_xmp_over_biased_dsm():
    from types import SimpleNamespace

    from ms_mosaic.camera import Pose
    from ms_mosaic.scene import Block, ImageRef

    # 绝对高 140、相对航高 66.6 → ground_z=73.4；若误用 DSM 中位 57 会得到 ~83
    block = Block(crs="EPSG:32650", ground_z=73.4)
    block.images = [
        ImageRef(0, 0, "Color", Path("a.jpg"), 0),
        ImageRef(1, 1, "Color", Path("b.jpg"), 0),
    ]
    block.gps = {0: np.array([0.0, 0.0, 140.0]), 1: np.array([0.0, 0.0, 140.2])}
    at = SimpleNamespace(
        poses={
            0: Pose.from_ypr(np.array([0.0, 0.0, 141.0]), 0.0, -90.0, 0.0),
            1: Pose.from_ypr(np.array([0.0, 0.0, 141.2]), 0.0, -90.0, 0.0),
        }
    )
    dsm = SimpleNamespace(stats={"z_median": 57.0})
    agl = mean_flight_agl_m(block, at, dsm)
    assert abs(agl - (140.1 - 73.4)) < 1e-9


def test_crs_label_matches_limapper():
    assert crs_label("EPSG:32650") == "[EPSG::32650] UTM 50N (WGS84), egm_none"
    assert crs_label("EPSG:32647") == "[EPSG::32647] UTM 47N (WGS84), egm_none"


def test_gps_histogram_percentages_sum_to_100():
    rows = [
        {"error": 1.0, "dx": 0.5, "dy": -0.5, "dz": 5.0},
        {"error": 3.0, "dx": 2.5, "dy": 0.2, "dz": 11.0},
        {"error": 15.0, "dx": 0.1, "dy": -1.5, "dz": 14.5},
        {"error": 0.4, "dx": -0.2, "dy": 0.1, "dz": 0.3},
    ]
    hist = gps_histogram(rows)
    assert hist[0]["label"].startswith("[-6")
    for key in ("error", "dx", "dy", "dz"):
        total = sum(b[key] for b in hist)
        assert abs(total - 100.0) < 1e-6


def test_gps_extrema_picks_min_abs_axis_and_max_3d():
    rows = [
        {"index": 1, "name": "a.tif", "gps0": [0, 0, 0], "gps1": [0.01, 2.0, 3.0],
         "error": 3.606, "dx": 0.01, "dy": 2.0, "dz": 3.0},
        {"index": 2, "name": "b.tif", "gps0": [0, 0, 0], "gps1": [5.0, 0.02, 1.0],
         "error": 5.099, "dx": 5.0, "dy": 0.02, "dz": 1.0},
        {"index": 3, "name": "c.tif", "gps0": [0, 0, 0], "gps1": [1.0, 1.0, 0.01],
         "error": 1.415, "dx": 1.0, "dy": 1.0, "dz": 0.01},
    ]
    ext = gps_extrema(rows)
    assert ext["min_x"]["index"] == 1
    assert ext["min_y"]["index"] == 2
    assert ext["min_z"]["index"] == 3
    assert ext["min_err"]["index"] == 3
    assert ext["max_x"]["index"] == 2
    assert ext["max_err"]["index"] == 2
    line = format_gps_extrema_line(ext["min_x"])
    assert "序号 = 1" in line and "a.tif" in line and "dx =" in line


def test_split_strips_breaks_on_turn_and_gap():
    # 两条平行航线，各 4 点，中间大转向 + 大间距
    xs = [0, 10, 20, 30, 30, 20, 10, 0]
    ys = [0, 0, 0, 0, 80, 80, 80, 80]
    yaws = [90, 90, 90, 90, -90, -90, -90, -90]
    centers = np.column_stack([xs, ys, np.zeros(8)])
    strips = split_strips(list(range(8)), centers, np.array(yaws, float))
    assert len(strips) == 2
    assert strips[0] == [0, 1, 2, 3]
    assert strips[1] == [4, 5, 6, 7]


def test_connected_sequences_orders_large_component_first():
    from ms_mosaic.matching import PairMatches

    matches = [
        PairMatches(0, 1, np.array([[0, 0]]), "essential"),
        PairMatches(1, 2, np.array([[0, 0]]), "essential"),
        PairMatches(4, 5, np.array([[0, 0]]), "essential"),
    ]
    seqs = connected_sequences(matches, [0, 1, 2, 3, 4, 5])
    assert seqs[0] == [0, 1, 2]
    assert [3] in seqs
    assert [4, 5] in seqs or seqs[1] == [4, 5]


def test_truncate_rows_keeps_head_and_tail():
    rows = list(range(40))
    out = truncate_rows(rows, head=5, tail=3)
    assert out[0] == 0 and out[4] == 4
    assert out[5] is None
    assert out[-3:] == [37, 38, 39]


def test_keypoint_series_covers_all_images_with_zero_default():
    from ms_mosaic.qa import keypoint_series
    from ms_mosaic.scene import Block, ImageRef

    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.images = [
        ImageRef(0, 0, "Color", Path("c.tif"), 0),
        ImageRef(1, 0, "650nm", Path("m.tif"), 3),
    ]
    series = keypoint_series(block, {0: 8192})
    assert series == [(0, 8192), (1, 0)]


def test_band_rms_accepts_residual_array():
    """空三残差是 ndarray。用 or [] 会触发「数组真值不明确」。"""
    from types import SimpleNamespace

    from ms_mosaic.at import Observation
    from ms_mosaic.qa import band_rms_px
    from ms_mosaic.scene import Block, ImageRef

    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.cameras = {"Color": object(), "550nm": object()}
    block.images = [
        ImageRef(0, 0, "Color", Path("c.tif"), 0),
        ImageRef(1, 0, "550nm", Path("g.tif"), 1),
    ]
    at = SimpleNamespace(
        stats={"rms_reprojection_px": 0.4},
        residuals_px=np.array([0.3, 0.5]),
        observations=[
            Observation(0, 0, np.zeros(2)),
            Observation(1, 0, np.zeros(2)),
        ],
    )
    rms = band_rms_px(block, at)
    assert abs(rms["Color"] - 0.3) < 1e-9
    assert abs(rms["550nm"] - 0.5) < 1e-9


def test_registered_image_ids_prefer_observations():
    from types import SimpleNamespace

    from ms_mosaic.at import Observation
    from ms_mosaic.qa import registered_image_ids

    at = SimpleNamespace(
        observations=[Observation(2, 0, np.zeros(2)), Observation(5, 1, np.zeros(2))],
        stats={},
        poses={2: None, 5: None, 9: None},
    )
    assert registered_image_ids(at) == {2, 5}


def test_split_strips_absorbs_singleton_when_yaw_close():
    # 5 点同航向，中间人为大间距本会切段；单张段应并回
    xs = [0, 10, 20, 50, 60]
    ys = [0, 0, 0, 0, 0]
    yaws = [90, 90, 90, 90, 90]
    centers = np.column_stack([xs, ys, np.zeros(5)])
    strips = split_strips(list(range(5)), centers, np.array(yaws, float), gap_factor=2.0)
    assert all(len(s) >= 2 for s in strips) or len(strips) == 1
    assert sum(len(s) for s in strips) == 5


def test_cameras_table_uses_per_band_at_intrinsics():
    """全波段空三后各组用各自内参，不再整表拷贝 Color。"""
    from types import SimpleNamespace

    from ms_mosaic.camera import Camera, Pose
    from ms_mosaic.qa import cameras_table
    from ms_mosaic.scene import Block, ImageRef

    cam_c = Camera.initial("Color", 1440, 1080, kind="rgb").with_vector(
        np.array([1800.0, 720.0, 540.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    )
    cam_g = Camera.initial("550nm", 1440, 1080, kind="ms").with_vector(
        np.array([1600.0, 710.0, 530.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    )
    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.cameras = {"Color": cam_c, "550nm": cam_g}
    block.images = [
        ImageRef(0, 0, "Color", Path("c.tif"), 0),
        ImageRef(1, 0, "550nm", Path("g.tif"), 1),
    ]
    pose = Pose(rotation=np.eye(3), center=np.array([0.0, 0.0, 80.0]))
    at = SimpleNamespace(
        cameras={"Color": cam_c, "550nm": cam_g},
        poses={0: pose, 1: pose},
    )
    table = cameras_table(block, at, rms_px=0.4)
    assert abs(table["Color"]["f"] - 1800.0) < 1e-9
    assert abs(table["550nm"]["f"] - 1600.0) < 1e-9
    assert table["Color"]["n_registered"] == 1
    assert table["550nm"]["n_registered"] == 1
