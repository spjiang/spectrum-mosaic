import math

import numpy as np
import pytest

from ms_mosaic.camera import (
    Camera,
    Pose,
    footprint,
    intersect_plane,
    project,
    rotation_from_ypr,
    wrap_yaw_deg,
    ypr_from_rotation,
)


def test_wrap_yaw_handles_xmp_outliers():
    # XMP 里实测出现过 -516.8
    assert wrap_yaw_deg(-516.8) == pytest.approx(-156.8, abs=1e-9)
    assert wrap_yaw_deg(184.1) == pytest.approx(-175.9, abs=1e-9)
    assert wrap_yaw_deg(-13.1) == pytest.approx(-13.1, abs=1e-9)


def test_nadir_north_rotation_is_canonical():
    r = rotation_from_ypr(0.0, -90.0, 0.0)
    np.testing.assert_allclose(r, np.diag([1.0, -1.0, -1.0]), atol=1e-12)


def test_rotation_is_orthonormal_for_real_attitudes():
    for yaw, pitch, roll in [(-13.1, -89.2, -1.0), (80.7, -91.7, -1.0), (184.1, -88.5, -0.5)]:
        r = rotation_from_ypr(yaw, pitch, roll)
        np.testing.assert_allclose(r @ r.T, np.eye(3), atol=1e-12)
        assert np.linalg.det(r) == pytest.approx(1.0, abs=1e-12)


def test_ypr_roundtrip():
    for yaw, pitch, roll in [(-13.1, -89.2, -1.0), (80.7, -85.0, 3.0), (-156.8, -90.0, 0.0)]:
        r = rotation_from_ypr(yaw, pitch, roll)
        y2, p2, r2 = ypr_from_rotation(r)
        assert y2 == pytest.approx(yaw, abs=1e-6)
        assert p2 == pytest.approx(pitch, abs=1e-6)
        assert r2 == pytest.approx(roll, abs=1e-6)


def test_nadir_camera_projects_center_to_principal_point():
    cam = Camera.initial("550nm", 2048, 1536)
    pose = Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.5]), 0.0, -90.0, 0.0)
    ground = np.array([[673000.0, 2620000.0, 1760.0]])
    u, v, valid = project(cam, pose, ground)
    assert valid[0]
    assert u[0] == pytest.approx(cam.cx, abs=1e-9)
    assert v[0] == pytest.approx(cam.cy, abs=1e-9)


def test_nadir_image_x_is_east_and_y_is_south():
    cam = Camera.initial("550nm", 2048, 1536)
    center = np.array([673000.0, 2620000.0, 1871.5])
    pose = Pose.from_ypr(center, 0.0, -90.0, 0.0)
    agl = 111.8
    ground = np.array(
        [
            [673000.0 + 10.0, 2620000.0, center[2] - agl],  # 正东
            [673000.0, 2620000.0 + 10.0, center[2] - agl],  # 正北
        ]
    )
    u, v, _ = project(cam, pose, ground)
    assert u[0] > cam.cx and v[0] == pytest.approx(cam.cy, abs=1e-9)  # 东 → 像素右
    assert v[1] < cam.cy and u[1] == pytest.approx(cam.cx, abs=1e-9)  # 北 → 像素上


def test_gsd_matches_commercial_scale():
    """f 初值下的 GSD 应接近商业正射的 0.0539 m。"""
    cam = Camera.initial("Color", 2048, 1536, kind="rgb")
    agl = 111.8
    assert agl / cam.f == pytest.approx(0.0521, abs=0.003)


def test_distortion_roundtrip():
    cam = Camera.initial("550nm", 2048, 1536).with_vector(
        np.array([2145.0, 1020.0, 770.0, -0.133, 0.130, 0.063, -3.2e-4, -3.5e-4, 0.0457, 0.2088])
    )
    u = np.array([5.0, 512.0, 1024.0, 2040.0])
    v = np.array([5.0, 400.0, 768.0, 1530.0])
    xn, yn = cam.undistort_pixels(u, v)
    u2, v2 = cam.project_normalized(xn, yn)
    np.testing.assert_allclose(u2, u, atol=1e-6)
    np.testing.assert_allclose(v2, v, atol=1e-6)


def test_projection_inverts_ray_intersection():
    cam = Camera.initial("550nm", 2048, 1536).with_vector(
        np.array([2100.0, 1024.0, 768.0, -0.14, 0.15, 0.08, 1e-4, -2e-4, 0.3, -0.1])
    )
    pose = Pose.from_ypr(np.array([673500.0, 2620200.0, 1871.5]), -13.1, -89.2, -1.0)
    u = np.array([100.0, 1024.0, 1900.0])
    v = np.array([80.0, 768.0, 1450.0])
    pts = intersect_plane(cam, pose, u, v, 1750.0)
    np.testing.assert_allclose(pts[:, 2], 1750.0, atol=1e-6)
    u2, v2, valid = project(cam, pose, pts)
    assert valid.all()
    np.testing.assert_allclose(u2, u, atol=1e-5)
    np.testing.assert_allclose(v2, v, atol=1e-5)


def test_distortion_fold_outside_format_is_invalid():
    """像幅外的 Brown 回折不能当成有效像素。

    本测区 k1/k2/k3 在归一化半径约 1.88 处回折。正下视相机正下方的地面
    应有效；水平距 300 m 的地面会折回像幅中央，必须判无效，否则边缘真正射
    被拉成油彩。
    """
    cam = Camera.initial("Color", 2048, 1536, kind="rgb").with_vector(
        np.array(
            [2408.9, 1009.0, 787.87, -0.156122, 0.226884, -0.043664, -2.2e-4, 4.1e-4, -0.31, 0.034]
        )
    )
    center = np.array([674386.0, 2620426.0, 1872.0])
    pose = Pose.from_ypr(center, 0.0, -90.0, 0.0)
    # 相机系 (-233, 184, 128)：水平距约 300 m，本测区畸变把它折回像幅中央
    far = center + np.array([-233.0, -184.0, -128.0])
    ground = np.array([center + np.array([0.0, 0.0, -129.0]), far])
    u, v, valid = project(cam, pose, ground)
    assert valid[0]
    assert not valid[1]
    assert cam.in_bounds(np.array([u[1]]), np.array([v[1]]))[0]


def test_footprint_size_matches_agl_and_focal():
    cam = Camera.initial("Color", 2048, 1536, kind="rgb")
    agl = 111.8
    pose = Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.5]), 0.0, -90.0, 0.0)
    poly = footprint(cam, pose, 1871.5 - agl)
    width_m = abs(poly[1, 0] - poly[0, 0])
    height_m = abs(poly[0, 1] - poly[3, 1])
    assert width_m == pytest.approx(2047 * agl / cam.f, rel=1e-6)
    assert height_m == pytest.approx(1535 * agl / cam.f, rel=1e-6)
    # 约 107 m x 80 m，和商业成品 710x642 m 区域 / 668 张的密度自洽
    assert 100.0 < width_m < 115.0


def test_yaw_rotates_footprint():
    cam = Camera.initial("550nm", 2048, 1536)
    pose_n = Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.5]), 0.0, -90.0, 0.0)
    pose_e = Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.5]), 90.0, -90.0, 0.0)
    fn = footprint(cam, pose_n, 1760.0)
    fe = footprint(cam, pose_e, 1760.0)
    # 航向转 90°：朝北时像幅宽沿东西方向，朝东时同一条边改为沿南北方向
    assert abs(fn[1, 0] - fn[0, 0]) == pytest.approx(abs(fe[1, 1] - fe[0, 1]), rel=1e-9)
    assert abs(fe[1, 0] - fe[0, 0]) == pytest.approx(0.0, abs=1e-9)


def test_viewing_direction_is_down_for_nadir():
    pose = Pose.from_ypr(np.array([0.0, 0.0, 100.0]), 37.0, -90.0, 0.0)
    np.testing.assert_allclose(pose.viewing_direction, np.array([0.0, 0.0, -1.0]), atol=1e-12)


def test_large_batch_projection_raises_no_spurious_float_warnings():
    """Apple Accelerate 的 BLAS 在大矩阵乘时会误置浮点异常标志位。
    热点已改用 einsum 规避；这条测试防止以后有人改回 @ 又把告警引回来。"""
    cam = Camera.initial("550nm", 2048, 1536)
    pose = Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.5]), -13.1, -89.2, -1.0)
    n = 300000
    rng = np.random.default_rng(0)
    pts = np.stack(
        [
            673000.0 + rng.normal(0, 50, n),
            2620000.0 + rng.normal(0, 50, n),
            1760.0 + rng.normal(0, 20, n),
        ],
        axis=1,
    )
    old = np.seterr(all="raise")
    try:
        u, v, valid = project(cam, pose, pts)
        uu = np.linspace(0, cam.width - 1, n)
        vv = np.linspace(0, cam.height - 1, n)
        intersect_plane(cam, pose, uu, vv, 1760.0)
    finally:
        np.seterr(**old)
    assert np.isfinite(u).all() and np.isfinite(v).all()


def test_off_nadir_angle_from_tilted_pose():
    pose = Pose.from_ypr(np.array([0.0, 0.0, 100.0]), 0.0, -80.0, 0.0)
    d = pose.viewing_direction
    tilt = math.degrees(math.acos(min(1.0, -d[2])))
    assert tilt == pytest.approx(10.0, abs=1e-9)
