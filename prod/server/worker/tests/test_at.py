"""空三测试。用合成场景造出已知真值，检验交会与平差能否把它找回来。

合成参数刻意贴近本批数据：2048x1536、航高 111.8 m、地形起伏、GPS 3 m 噪声。
"""

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from ms_mosaic.at import (
    ATResult,
    BAProblem,
    Observation,
    _BA_RESERVE_MB,
    _BA_WORKER_MB,
    _assemble_band,
    _damp_solve_band,
    _separable_bands,
    ba_parallel_workers,
    band_worker_bytes,
    drop_outliers,
    run_at,
    solve_lm,
    triangulate_track,
    triangulate_tracks,
)
from ms_mosaic.camera import Camera, Pose, project
from ms_mosaic.features import Features
from ms_mosaic.tracks import Tracks

TRUE_INTRINSICS = np.array(
    [2100.0, 1030.0, 760.0, -0.133, 0.130, 0.063, -3.2e-4, -3.5e-4, 0.046, 0.209]
)
FLIGHT_Z = 1871.5
GROUND_Z = 1759.7


def _true_camera() -> Camera:
    return Camera.initial("Color", 2048, 1536, kind="rgb").with_vector(TRUE_INTRINSICS)


def _scene(seed: int = 0, n_points: int = 900, grid=(6, 4), step: float = 22.0, relief: float = 55.0):
    """返回 (真相机, 真位姿, 真物方点)。

    地形起伏默认 55 m。实测 MAX_20251017_001 的地形 p1–p99 起伏 161 m、航高仅
    111.8 m，起伏对焦距的可观测性影响很大：起伏越小，焦距与航高越接近共线，
    自标定出的 f 越不可靠。这里取一个偏保守的值。
    """
    rng = np.random.default_rng(seed)
    cols, rows = grid
    poses = {}
    idx = 0
    for r in range(rows):
        for c in range(cols):
            center = np.array([673000.0 + c * step, 2620000.0 + r * step, FLIGHT_Z])
            yaw = 0.0 if r % 2 == 0 else 180.0  # 往返航线
            poses[idx] = Pose.from_ypr(center, yaw, -90.0, 0.0)
            idx += 1

    span_x = (grid[0] - 1) * step
    span_y = (grid[1] - 1) * step
    xs = 673000.0 - 50.0 + rng.random(n_points) * (span_x + 100.0)
    ys = 2620000.0 - 50.0 + rng.random(n_points) * (span_y + 100.0)
    zs = (
        GROUND_Z
        + relief * np.sin(xs / 40.0) * np.cos(ys / 55.0)
        + rng.normal(0, 1.0, n_points)
    )
    return _true_camera(), poses, np.stack([xs, ys, zs], axis=1)


def _observe(cam, poses, points, *, pixel_noise=0.3, seed=1):
    """投影出观测，组装成 tracks / features 结构。"""
    rng = np.random.default_rng(seed)
    per_point: dict[int, list[tuple[int, int]]] = {}
    keypoints: dict[int, list[np.ndarray]] = {i: [] for i in poses}
    for pid in range(points.shape[0]):
        for img, pose in poses.items():
            u, v, valid = project(cam, pose, points[pid : pid + 1])
            if not valid[0]:
                continue
            uu = u[0] + rng.normal(0, pixel_noise)
            vv = v[0] + rng.normal(0, pixel_noise)
            if not cam.in_bounds(np.array([uu]), np.array([vv]))[0]:
                continue
            fid = len(keypoints[img])
            keypoints[img].append(np.array([uu, vv, 4.0, 0.0]))
            per_point.setdefault(pid, []).append((img, fid))

    observations = [sorted(v) for v in per_point.values() if len(v) >= 2]
    features = {
        i: Features(np.array(kp).reshape(-1, 4), np.zeros((len(kp), 128), np.float32))
        for i, kp in keypoints.items()
    }
    return Tracks(observations), features


def _perturb(poses, *, pos_sigma=2.0, att_sigma_deg=0.6, seed=2):
    """返回 (扰动后的位姿, GPS 观测, IMU 姿态观测)，模拟真实的 POS 初值质量。"""
    rng = np.random.default_rng(seed)
    out, gps, att = {}, {}, {}
    for i, pose in poses.items():
        dc = rng.normal(0, pos_sigma, 3)
        drot = Rotation.from_rotvec(np.radians(rng.normal(0, att_sigma_deg, 3)))
        rot = drot.as_matrix() @ pose.rotation
        out[i] = Pose(rot, pose.center + dc)
        gps[i] = pose.center + rng.normal(0, pos_sigma, 3)
        att[i] = rot
    return out, gps, att


def test_triangulate_recovers_known_point():
    cam, poses, _ = _scene()
    target = np.array([[673040.0, 2620030.0, 1762.0]])
    cams, pss, uvs = [], [], []
    for pose in list(poses.values())[:6]:
        u, v, valid = project(cam, pose, target)
        if not valid[0] or not cam.in_bounds(u, v)[0]:
            continue
        cams.append(cam)
        pss.append(pose)
        uvs.append([u[0], v[0]])
    assert len(cams) >= 2
    point, angle = triangulate_track(cams, pss, np.array(uvs))
    np.testing.assert_allclose(point, target[0], atol=0.05)
    assert angle > 1.0


def test_triangulate_reports_small_angle_for_near_parallel_rays():
    cam = _true_camera()
    target = np.array([[673000.0, 2620000.0, 1760.0]])
    poses = [
        Pose.from_ypr(np.array([673000.0, 2620000.0, FLIGHT_Z]), 0.0, -90.0, 0.0),
        Pose.from_ypr(np.array([673000.3, 2620000.0, FLIGHT_Z]), 0.0, -90.0, 0.0),
    ]
    uvs = []
    for pose in poses:
        u, v, _ = project(cam, pose, target)
        uvs.append([u[0], v[0]])
    _, angle = triangulate_track([cam, cam], poses, np.array(uvs))
    assert angle < 1.0


def _small_problem(seed: int = 5):
    """构造一个小规模平差问题，供雅可比对拍使用。"""
    from ms_mosaic.at import BAProblem, triangulate_tracks

    cam_true, poses_true, points_true = _scene(seed=seed, n_points=60, grid=(3, 2))
    tracks, features = _observe(cam_true, poses_true, points_true, pixel_noise=0.4, seed=seed + 1)
    poses_init, gps, att = _perturb(poses_true, seed=seed + 2)
    cameras = {"Color": Camera.initial("Color", 2048, 1536, kind="rgb")}
    per_image = {i: cameras["Color"] for i in poses_init}
    points, observations = triangulate_tracks(tracks, per_image, poses_init, features)
    problem = BAProblem(
        cameras,
        poses_init,
        points,
        observations,
        {i: "Color" for i in poses_init},
        gps,
        att,
    )
    return problem


def test_triangulate_tracks_keypoints_only_matches_features():
    """交会只依赖关键点坐标，与完整 Features 数值一致。"""
    cam_true, poses_true, points_true = _scene(seed=7, n_points=40, grid=(2, 2))
    tracks, features = _observe(cam_true, poses_true, points_true, pixel_noise=0.2, seed=8)
    poses_init, _, _ = _perturb(poses_true, seed=9)
    cams = {i: Camera.initial("Color", 2048, 1536, kind="rgb") for i in poses_init}
    pts_full, obs_full = triangulate_tracks(tracks, cams, poses_init, features)
    keypoints = {i: f.keypoints for i, f in features.items()}
    pts_kps, obs_kps = triangulate_tracks(tracks, cams, poses_init, keypoints)
    assert pts_full.shape == pts_kps.shape
    assert len(obs_full) == len(obs_kps)
    if len(pts_full):
        assert np.allclose(pts_full, pts_kps, atol=1e-9)


def _triangulate_tracks_loop(tracks, cameras, poses, keypoints, min_angle_deg=1.0):
    """逐条交会的参照实现（向量化之前的主路径）。"""
    points, observations = [], []
    for obs in tracks.observations:
        obs = [(i, f) for i, f in obs if i in poses and i in cameras and i in keypoints]
        if len(obs) < 2:
            continue
        pss = [poses[i] for i, _ in obs]
        uvs = np.array([keypoints[i][f, :2] for i, f in obs], dtype=float)
        point, angle = triangulate_track([cameras[i] for i, _ in obs], pss, uvs)
        if not np.all(np.isfinite(point)) or angle < min_angle_deg:
            continue
        if any(p.world_to_camera(point[None, :])[0, 2] <= 0 for p in pss):
            continue
        points.append(point)
        for (image, _), uv in zip(obs, uvs):
            observations.append(Observation(image, len(points) - 1, uv))
    return np.array(points).reshape(-1, 3), observations


def test_triangulate_tracks_vectorized_matches_loop():
    """批量 DLT 与逐条交会：同一批点、同一顺序、同一观测。"""
    cam_true, poses_true, points_true = _scene(seed=11, n_points=300, grid=(4, 3))
    tracks, features = _observe(cam_true, poses_true, points_true, pixel_noise=0.3, seed=12)
    poses_init, _, _ = _perturb(poses_true, seed=13)
    cam_a = Camera.initial("Color", 2048, 1536, kind="rgb").with_vector(TRUE_INTRINSICS)
    cam_b = Camera.initial("450", 2048, 1536, kind="rgb")
    cams = {i: (cam_a if i % 2 else cam_b) for i in poses_init}
    # 去掉一张的位姿：轨迹要跳过它的观测，可能因此不足两视
    poses_init.pop(5)
    # 近平行两视：交会角 < 1° 必须被丢弃
    tracks.observations.append([(0, 0), (0, 1)])
    keypoints = {i: f.keypoints for i, f in features.items()}
    ref_pts, ref_obs = _triangulate_tracks_loop(tracks, cams, poses_init, keypoints)
    pts, obs = triangulate_tracks(tracks, cams, poses_init, keypoints)
    assert pts.shape == ref_pts.shape and len(pts) > 100
    np.testing.assert_allclose(pts, ref_pts, rtol=0, atol=1e-9)
    assert [(o.image, o.point) for o in obs] == [(o.image, o.point) for o in ref_obs]
    assert all(np.array_equal(a.uv, b.uv) for a, b in zip(obs, ref_obs))


def test_run_at_clears_features_after_triangulation():
    """交会后清空关键点字典，给平差腾峰值内存。"""
    cam_true, poses_true, points_true = _scene(seed=3, n_points=30, grid=(2, 2))
    tracks, features = _observe(cam_true, poses_true, points_true, pixel_noise=0.2, seed=4)
    poses_init, gps, att = _perturb(poses_true, seed=5)
    cameras = {"Color": Camera.initial("Color", 2048, 1536, kind="rgb")}
    keypoints = {i: f.keypoints.copy() for i, f in features.items()}
    result = run_at(
        cameras,
        poses_init,
        tracks,
        keypoints,
        {i: "Color" for i in poses_init},
        gps,
        att,
        sigma_xyz=(3.0, 3.0, 5.0),
        stages=((),),
        verbose=0,
    )
    assert len(keypoints) == 0
    assert len(result.points) > 0


def test_analytic_jacobian_matches_finite_differences():
    """解析雅可比一旦写错，表现只是收敛得差一点，很难反推，必须直接对拍。"""
    problem = _small_problem()
    x = problem.x0.copy()
    rng = np.random.default_rng(11)
    # 从初值稍微挪开，避免恰好落在 δ=0 这种特殊点上
    x[: problem.off_center] += rng.normal(0, 0.002, problem.off_center)
    x[problem.off_center : problem.off_intr] += rng.normal(0, 0.5, problem.n_img * 3)
    x[problem.off_point :] += rng.normal(0, 0.3, problem.n_pts * 3)

    analytic = np.asarray(problem.jacobian(x).todense())
    base = problem.residuals(x)

    # 只抽查部分列，全列数值微分太慢
    columns = sorted(
        set(
            list(range(0, 6))
            + list(range(problem.off_center, problem.off_center + 6))
            + list(range(problem.off_intr, problem.off_point))
            + list(range(problem.off_point, problem.off_point + 9))
        )
    )
    for col in columns:
        step = max(1e-7, abs(x[col]) * 1e-6)
        xp = x.copy()
        xp[col] += step
        numeric = (problem.residuals(xp) - base) / step
        got = analytic[:, col]
        scale = max(1.0, float(np.abs(numeric).max()))
        assert np.abs(got - numeric).max() / scale < 2e-4, f"第 {col} 列雅可比不匹配"


def test_drop_outliers_removes_bad_observations_and_orphan_points():
    points = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]])
    obs = [
        Observation(0, 0, np.array([1.0, 1.0])),
        Observation(1, 0, np.array([1.0, 1.0])),
        Observation(0, 1, np.array([1.0, 1.0])),
        Observation(1, 1, np.array([1.0, 1.0])),
        Observation(0, 2, np.array([1.0, 1.0])),
        Observation(1, 2, np.array([1.0, 1.0])),
    ]
    residual = np.array([0.2, 0.3, 0.1, 99.0, 0.4, 0.5])
    pts, keep, dropped = drop_outliers(points, obs, residual, threshold_px=6.0)
    assert dropped == 1
    assert pts.shape[0] == 2  # 点 1 只剩一个观测，被丢弃
    assert {o.point for o in keep} == {0, 1}
    np.testing.assert_allclose(pts[0], points[0])
    np.testing.assert_allclose(pts[1], points[2])


def test_bundle_adjust_per_camera_matches_joint():
    """波段互不共享物方点时，逐波段独立 LM 与整块 LM 收敛到同一解。"""
    from ms_mosaic.at import bundle_adjust

    inputs = _two_band_inputs()
    cameras, poses, points, observations, image_camera, gps, att = inputs
    split = bundle_adjust(
        cameras, poses, points, observations, image_camera, gps, att,
        free_names=("f", "cx", "cy"), max_iterations=30,
    )
    problem = BAProblem(
        cameras, poses, points, observations, image_camera, gps, att, free_names=("f", "cx", "cy")
    )
    x = solve_lm(problem, max_iterations=30, band_parallel=False)
    j_cams, j_poses, j_pts = problem.unpack(x)
    for key in cameras:
        np.testing.assert_allclose(split[0][key].to_vector(), j_cams[key].to_vector(), rtol=1e-4, atol=1e-3)
    for i in poses:
        np.testing.assert_allclose(split[1][i].center, j_poses[i].center, atol=1e-3)
    np.testing.assert_allclose(split[2], j_pts, atol=1e-3)
    # 两套 LM 停在收敛判据附近的不同点，逐观测残差差在 0.01 px 内
    np.testing.assert_allclose(split[3], problem.reprojection_px(x), atol=1e-2)


def _two_band_inputs():
    cam_a, poses_a, points_a = _scene(seed=3, n_points=40, grid=(3, 2), step=18.0)
    cam_b = Camera.initial("450", 2048, 1536, kind="ms").with_vector(TRUE_INTRINSICS * np.array(
        [0.55, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    ))
    poses_b = {i + 100: Pose(p.rotation, p.center + np.array([0.0, 80.0, 0.0])) for i, p in poses_a.items()}
    points_b = points_a + np.array([0.0, 80.0, 0.0])
    tracks_a, feats_a = _observe(cam_a, poses_a, points_a, pixel_noise=0.2, seed=4)
    tracks_b, feats_b = _observe(cam_b, poses_b, points_b, pixel_noise=0.2, seed=6)
    init_a, gps_a, att_a = _perturb(poses_a, pos_sigma=1.0, att_sigma_deg=0.3, seed=7)
    init_b, gps_b, att_b = _perturb(poses_b, pos_sigma=1.0, att_sigma_deg=0.3, seed=8)
    cameras = {"Color": Camera.initial("Color", 2048, 1536, kind="rgb"), "450": Camera.initial("450", 2048, 1536, kind="ms")}
    image_camera = {i: "Color" for i in poses_a}
    image_camera.update({i: "450" for i in poses_b})
    points_ta, obs_a = triangulate_tracks(tracks_a, {i: cameras["Color"] for i in poses_a}, init_a, feats_a)
    points_tb, obs_b = triangulate_tracks(tracks_b, {i: cameras["450"] for i in poses_b}, init_b, feats_b)
    shift = len(points_ta)
    obs_b = [Observation(o.image, o.point + shift, o.uv) for o in obs_b]
    return (
        cameras,
        {**init_a, **init_b},
        np.vstack([points_ta, points_tb]),
        obs_a + obs_b,
        image_camera,
        {**gps_a, **gps_b},
        {**att_a, **att_b},
    )


def _two_band_problem():
    """两个波段各看各的点，法方程应当块对角。"""
    cam_a, poses_a, points_a = _scene(seed=3, n_points=40, grid=(3, 2), step=18.0)
    cam_b = Camera.initial("450", 2048, 1536, kind="ms").with_vector(TRUE_INTRINSICS * np.array(
        [0.55, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    ))
    poses_b = {}
    for i, pose in poses_a.items():
        poses_b[i + 100] = Pose(pose.rotation, pose.center + np.array([0.0, 80.0, 0.0]))
    points_b = points_a + np.array([0.0, 80.0, 0.0])
    tracks_a, feats_a = _observe(cam_a, poses_a, points_a, pixel_noise=0.2, seed=4)
    tracks_b, feats_b = _observe(cam_b, poses_b, points_b, pixel_noise=0.2, seed=6)
    init_a, gps_a, att_a = _perturb(poses_a, pos_sigma=1.0, att_sigma_deg=0.3, seed=7)
    init_b, gps_b, att_b = _perturb(poses_b, pos_sigma=1.0, att_sigma_deg=0.3, seed=8)
    poses_init = {**init_a, **init_b}
    gps = {**gps_a, **gps_b}
    att = {**att_a, **att_b}
    cameras = {"Color": Camera.initial("Color", 2048, 1536, kind="rgb"), "450": Camera.initial("450", 2048, 1536, kind="ms")}
    image_camera = {i: "Color" for i in poses_a}
    image_camera.update({i: "450" for i in poses_b})
    points_ta, obs_a = triangulate_tracks(tracks_a, {i: cameras["Color"] for i in poses_a}, init_a, feats_a)
    points_tb, obs_b = triangulate_tracks(tracks_b, {i: cameras["450"] for i in poses_b}, init_b, feats_b)
    # 第二组的点号接在第一组后面
    shift = len(points_ta)
    obs_b = [Observation(o.image, o.point + shift, o.uv) for o in obs_b]
    points = np.vstack([points_ta, points_tb])
    observations = obs_a + obs_b
    problem = BAProblem(
        cameras, poses_init, points, observations, image_camera, gps, att,
        free_names=("f", "cx", "cy"),
    )
    return problem


def test_band_parallel_matches_joint_solution():
    """分波段并行解出的步长必须和整块求解一致，否则就是把方程拆错了。"""
    problem = _two_band_problem()
    assert _separable_bands(problem) is not None
    assert len(_separable_bands(problem)) == 2
    joint = solve_lm(problem, max_iterations=1, band_parallel=False)
    split = solve_lm(problem, max_iterations=1, band_parallel=True)
    np.testing.assert_allclose(split, joint, rtol=1e-6, atol=1e-5)


def test_chunked_band_assembly_matches_single_pass():
    """按观测分块累加的法方程必须与一次组装逐项相同。"""
    problem = _two_band_problem()
    band = _separable_bands(problem)[0]
    one = _assemble_band(problem, band, problem.x0, 1.5, chunk=10**9)
    many = _assemble_band(problem, band, problem.x0, 1.5, chunk=7)
    np.testing.assert_allclose(many[0], one[0], rtol=1e-10, atol=1e-8)
    np.testing.assert_allclose(many[1].toarray(), one[1].toarray(), rtol=1e-10, atol=1e-8)
    for got, want in zip(many[2:], one[2:]):
        np.testing.assert_allclose(got, want, rtol=1e-10, atol=1e-8)


def test_band_worker_bytes_covers_measured_peak():
    """内存模型不能低估单路峰值，否则按它放出的并行路数会把引擎打满。"""
    import tracemalloc

    problem = _two_band_problem()
    band = _separable_bands(problem)[0]
    tracemalloc.start()
    tracemalloc.reset_peak()
    solved = _damp_solve_band(_assemble_band(problem, band, problem.x0, 1.5, chunk=50), 1e-3)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert solved is not None
    est = band_worker_bytes(int(band.obs_idx.size), band.n_pts, band.nc, problem.n_free, chunk=50)
    assert est >= peak


def test_ba_parallel_workers_uses_band_size(monkeypatch):
    """单路按波段规模估：1 GB 一路时 10 GB 剩余能开多路，而不是被 4 GB 固定值压成 1 路。"""
    monkeypatch.setattr("ms_mosaic.at.cpu_cap", lambda: 10)
    monkeypatch.setattr("ms_mosaic.at.job_memory_gb", lambda: 23.4)
    monkeypatch.setattr("ms_mosaic.at.resolve_memory_budget", lambda _gb: int(23.4 * 1024**3))
    monkeypatch.setattr("ms_mosaic.at.process_rss_bytes", lambda: int(4 * 1024**3))
    monkeypatch.setattr("ms_mosaic.memory.available_bytes", lambda: int(10 * 1024**3))
    n = ba_parallel_workers(8, per_worker_bytes=1024**3)
    assert n == (10 * 1024 - _BA_RESERVE_MB) // 1024


def test_ba_parallel_workers_subtracts_own_rss(monkeypatch):
    """任务预算是总量：本进程已占 20 GB 时，23.4 GB 预算只剩 3.4 GB 可分。"""
    monkeypatch.setattr("ms_mosaic.at.cpu_cap", lambda: 10)
    monkeypatch.setattr("ms_mosaic.at.job_memory_gb", lambda: 23.4)
    monkeypatch.setattr("ms_mosaic.at.resolve_memory_budget", lambda _gb: int(23.4 * 1024**3))
    monkeypatch.setattr("ms_mosaic.at.process_rss_bytes", lambda: int(20 * 1024**3))
    monkeypatch.setattr("ms_mosaic.memory.available_bytes", lambda: int(64 * 1024**3))
    n = ba_parallel_workers(8, per_worker_bytes=1024**3)
    assert n == max(1, (int(3.4 * 1024) - _BA_RESERVE_MB) // 1024)


def test_summarize_observations_per_image():
    at = ATResult(
        cameras={},
        poses={},
        points=np.zeros((2, 3)),
        observations=[
            Observation(5, 0, np.zeros(2)),
            Observation(5, 1, np.zeros(2)),
            Observation(9, 1, np.zeros(2)),
        ],
        residuals_px=np.array([1.0, 2.0, 3.0]),
    )
    at.summarize_observations(drop=True)
    assert at.observations == []
    assert at.obs_counts == {5: 2, 9: 1}
    assert at.obs_sq_residual == {5: 5.0, 9: 9.0}


def test_ba_parallel_workers_caps_by_memory(monkeypatch):
    """24GB 引擎上 8 波段不能全开，否则交会后的平差会被系统杀掉。"""
    monkeypatch.setattr("ms_mosaic.at.cpu_cap", lambda: 10)
    monkeypatch.setattr("ms_mosaic.at.job_memory_gb", lambda: 23.4)
    monkeypatch.setattr("ms_mosaic.at.process_rss_bytes", lambda: 0)
    monkeypatch.setattr(
        "ms_mosaic.at.resolve_memory_budget",
        lambda _gb: int(23.4 * 1024**3),
    )
    monkeypatch.setattr(
        "ms_mosaic.memory.available_bytes",
        lambda: int(10 * 1024**3),
    )
    n = ba_parallel_workers(8)
    assert n < 8
    assert n >= 1
    # 10GB 剩余 − 8GB 预留，最多再开 1 路 4GB 工人。
    assert n == max(1, (10 * 1024 - _BA_RESERVE_MB) // _BA_WORKER_MB)


def test_ba_parallel_workers_keeps_full_when_ram_plenty(monkeypatch):
    monkeypatch.setattr("ms_mosaic.at.cpu_cap", lambda: 10)
    monkeypatch.setattr("ms_mosaic.at.process_rss_bytes", lambda: 0)
    monkeypatch.setattr("ms_mosaic.at.job_memory_gb", lambda: 48.0)
    monkeypatch.setattr(
        "ms_mosaic.at.resolve_memory_budget",
        lambda _gb: int(48 * 1024**3),
    )
    monkeypatch.setattr(
        "ms_mosaic.memory.available_bytes",
        lambda: int(48 * 1024**3),
    )
    assert ba_parallel_workers(8) == 8


def test_cross_band_point_stays_joint():
    """同一个物方点被两个波段看到时不能拆开。"""
    problem = _two_band_problem()
    other = int(np.flatnonzero(problem.obs_camera != problem.obs_camera[0])[0])
    problem.obs_point = problem.obs_point.copy()
    problem.obs_point[0] = problem.obs_point[other]
    assert _separable_bands(problem) is None


def test_drop_outliers_keeps_everything_when_clean():
    points = np.array([[0.0, 0, 0]])
    obs = [Observation(0, 0, np.array([1.0, 1.0])), Observation(1, 0, np.array([2.0, 2.0]))]
    pts, keep, dropped = drop_outliers(points, obs, np.array([0.3, 0.4]))
    assert dropped == 0 and len(keep) == 2 and pts.shape[0] == 1


@pytest.mark.slow
def test_bundle_adjustment_recovers_intrinsics_and_poses():
    cam_true, poses_true, points_true = _scene()
    tracks, features = _observe(cam_true, poses_true, points_true)
    poses_init, gps, att = _perturb(poses_true)

    image_camera = {i: "Color" for i in poses_true}
    cameras = {"Color": Camera.initial("Color", 2048, 1536, kind="rgb")}

    result = run_at(
        cameras,
        poses_init,
        tracks,
        features,
        image_camera,
        gps,
        att,
        sigma_xyz=(3.0, 3.0, 5.0),
    )

    # 重投影精度应当达到商业软件量级（LiMapper 报告 RMS 0.41 px），
    # 观测噪声本身是 0.3 px，所以 0.5 px 已经接近噪声下限
    assert result.rms_reprojection_px < 0.5

    got = result.cameras["Color"]
    assert got.f == pytest.approx(TRUE_INTRINSICS[0], rel=0.02)
    assert got.cx == pytest.approx(TRUE_INTRINSICS[1], abs=5.0)
    assert got.cy == pytest.approx(TRUE_INTRINSICS[2], abs=5.0)
    assert got.k1 == pytest.approx(TRUE_INTRINSICS[3], abs=0.02)
    # b1/b2 是仿射项，正下视区块里几乎不可观测。LiMapper 报告里 8 个组的 b1
    # 从 0.046 跳到 4.94，商业软件同样定不住，这里不做断言。

    # 位姿应当比 2 m 的初值误差明显更好
    err = np.array(
        [np.linalg.norm(result.poses[i].center - poses_true[i].center) for i in poses_true]
    )
    assert float(np.median(err)) < 1.0
    assert result.stats["n_points"] > 300
    assert result.stats["gps_rmse_m"] < 3.0
