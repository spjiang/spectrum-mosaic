"""空中三角测量：多视前方交会 + GNSS 约束光束法平差 + 相机自标定。

策略
----
本批数据每帧都有 GNSS + IMU，因此不走经典增量式 SfM，而用 **GNSS/INS 全辅助 SfM**：
直接拿 POS 当外方位初值，交会出物方点后一次性全局平差。依据 Remote Sens. 2020,
12(3):351 —— 有 POS 时该策略比增量重建更稳也更快，且不会出现增量式常见的漂移。

长条带弱构型容易出现「碗状变形」（Remote Sens. 2021, 13(21):4222），对策是两条：
GNSS 位置先验直接进法方程，以及畸变参数分阶段释放而不是一上来全放开。

平差目标
--------
    min  Σ ρ(‖π(θ_c, R_i, C_i, X_j) − x_ij‖²)      重投影，Huber 核
       + Σ ‖(C_i − C_i^GPS) / σ_xyz‖²               GNSS 位置先验 σ=(3,3,5) m
       + Σ ‖(r_i − r_i^IMU) / σ_rpy‖²               IMU 姿态先验（弱权）

自标定参数 f, cx, cy, k1, k2, k3, p1, p2, b1, b2 与 LiMapper 报告的相机标定表一一对应。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix
from scipy.spatial.transform import Rotation

from ms_mosaic.camera import INTRINSIC_NAMES, Camera, Pose
from ms_mosaic.cpu import cpu_cap
from ms_mosaic.features import Features
from ms_mosaic.memory import cap_workers, job_memory_gb, resolve_memory_budget
from ms_mosaic.tracks import Tracks

OUTLIER_THRESHOLD_PX = 6.0  # 与 LiMapper 报告「外点阈值（像素） 6」一致
HUBER_SCALE_PX = 1.5
MIN_TRIANGULATION_ANGLE_DEG = 1.0
MIN_OBS_PER_TRACK = 2
# 单波段组装 Schur 法方程时雅可比/W 块峰值约数 GB。8 路一起上会在 24GB 引擎里被 oom_kill。
_BA_WORKER_MB = 4096
_BA_RESERVE_MB = 8192


def ba_parallel_workers(n_bands: int) -> int:
    """平差并行路数：波段数 ∩ CPU ∩（任务预算 ∩ 此刻剩余内存）。"""
    n = max(1, int(n_bands or 1))
    n = min(n, cpu_cap())
    budget = resolve_memory_budget(job_memory_gb())
    return cap_workers(
        n,
        per_worker_mb=_BA_WORKER_MB,
        reserve_mb=_BA_RESERVE_MB,
        budget_bytes=budget,
    )

# 分阶段释放内参，抑制长条带自标定的碗状变形
CALIBRATION_STAGES = (
    ("f", "cx", "cy"),
    ("f", "cx", "cy", "k1", "k2"),
    ("f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2"),
)

# least_squares 的参数尺度，量级差异大必须给，否则步长被 f 主导
INTRINSIC_SCALES = {
    "f": 50.0,
    "cx": 10.0,
    "cy": 10.0,
    "k1": 0.05,
    "k2": 0.05,
    "k3": 0.05,
    "p1": 5e-4,
    "p2": 5e-4,
    "b1": 1.0,
    "b2": 1.0,
}


@dataclass
class Observation:
    """一条 (影像, 物方点, 像点) 观测。"""

    image: int
    point: int
    uv: np.ndarray


@dataclass
class ATResult:
    cameras: dict[str, Camera]
    poses: dict[int, Pose]
    points: np.ndarray
    observations: list[Observation]
    residuals_px: np.ndarray
    stats: dict = field(default_factory=dict)

    @property
    def rms_reprojection_px(self) -> float:
        if self.residuals_px.size == 0:
            return float("nan")
        return float(np.sqrt(np.mean(self.residuals_px**2)))

    @property
    def mean_reprojection_px(self) -> float:
        if self.residuals_px.size == 0:
            return float("nan")
        return float(np.mean(self.residuals_px))


def _projection_matrix(pose: Pose) -> np.ndarray:
    p = np.zeros((3, 4))
    p[:, :3] = pose.rotation
    p[:, 3] = -pose.rotation @ pose.center
    return p


def triangulate_track(
    cameras: list[Camera], poses: list[Pose], uvs: np.ndarray
) -> tuple[np.ndarray, float]:
    """多视线性交会（DLT）。返回 (物方点, 最大交会角度数)。"""
    rows = []
    dirs = []
    for cam, pose, uv in zip(cameras, poses, uvs):
        xn, yn = cam.undistort_pixels(np.array([uv[0]]), np.array([uv[1]]))
        p = _projection_matrix(pose)
        rows.append(xn[0] * p[2] - p[0])
        rows.append(yn[0] * p[2] - p[1])
        d = pose.rotation.T @ np.array([xn[0], yn[0], 1.0])
        dirs.append(d / np.linalg.norm(d))
    _, _, vt = np.linalg.svd(np.array(rows))
    h = vt[-1]
    if abs(h[3]) < 1e-12:
        return np.array([np.nan, np.nan, np.nan]), 0.0
    point = h[:3] / h[3]

    best = 0.0
    for a in range(len(dirs)):
        for b in range(a + 1, len(dirs)):
            cosang = float(np.clip(np.dot(dirs[a], dirs[b]), -1.0, 1.0))
            best = max(best, np.degrees(np.arccos(cosang)))
    return point, best


def _keypoints_of(features: Features | np.ndarray) -> np.ndarray:
    return features.keypoints if isinstance(features, Features) else np.asarray(features)


def triangulate_tracks(
    tracks: Tracks,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    features: dict[int, Features] | dict[int, np.ndarray],
    *,
    min_angle_deg: float = MIN_TRIANGULATION_ANGLE_DEG,
) -> tuple[np.ndarray, list[Observation]]:
    """逐轨迹交会。交会角过小的点几何上不可靠，直接丢弃。

    ``features`` 可以是完整 Features，也可以只是关键点数组（交会不需要描述子）。
    方程与 ``triangulate_track`` 完全相同，仅减少描述子驻留。
    """
    keypoints = {i: _keypoints_of(feat) for i, feat in features.items()}
    points: list[np.ndarray] = []
    observations: list[Observation] = []
    for obs in tracks.observations:
        obs = [(i, f) for i, f in obs if i in poses and i in cameras and i in keypoints]
        if len(obs) < MIN_OBS_PER_TRACK:
            continue
        cams = [cameras[i] for i, _ in obs]
        pss = [poses[i] for i, _ in obs]
        uvs = np.array([keypoints[i][f, :2] for i, f in obs], dtype=float)
        point, angle = triangulate_track(cams, pss, uvs)
        if not np.all(np.isfinite(point)) or angle < min_angle_deg:
            continue
        # 交会点必须在所有相机前方
        if any(pose.world_to_camera(point[None, :])[0, 2] <= 0 for pose in pss):
            continue
        point_id = len(points)
        points.append(point)
        for (image, _), uv in zip(obs, uvs):
            observations.append(Observation(image, point_id, uv))
    return (np.array(points).reshape(-1, 3), observations)


def _skew(v: np.ndarray) -> np.ndarray:
    """批量反对称矩阵。v 形如 (N, 3)，返回 (N, 3, 3)。"""
    out = np.zeros(v.shape[:-1] + (3, 3))
    out[..., 0, 1] = -v[..., 2]
    out[..., 0, 2] = v[..., 1]
    out[..., 1, 0] = v[..., 2]
    out[..., 1, 2] = -v[..., 0]
    out[..., 2, 0] = -v[..., 1]
    out[..., 2, 1] = v[..., 0]
    return out


def _left_jacobian(delta: np.ndarray) -> np.ndarray:
    """SO(3) 左雅可比 J_l(δ) = I + (1−cosθ)/θ² [δ]× + (θ−sinθ)/θ³ [δ]×²，θ→0 退化为 I。

    本文件用左扰动 R = Exp(δ)·R_base，于是
        ∂(R·d)/∂δ = −Exp(δ)·[R_base·d]× · J_r(δ) = −[pc]× · J_l(δ)
    这里用到 R[a]× = [Ra]×R 以及 J_l = Exp(δ)·J_r。δ=0 时 J_l = I，
    所以漏掉 J_l 只会让收敛变慢而不报错，必须靠数值微分对拍才能发现。
    """
    theta = np.linalg.norm(delta, axis=-1)
    small = theta < 1e-8
    safe = np.where(small, 1.0, theta)
    k1 = np.where(small, 0.5, (1.0 - np.cos(safe)) / safe**2)
    k2 = np.where(small, 1.0 / 6.0, (safe - np.sin(safe)) / safe**3)
    sk = _skew(delta)
    eye = np.broadcast_to(np.eye(3), sk.shape)
    return eye + k1[..., None, None] * sk + k2[..., None, None] * (sk @ sk)


def _distortion_terms(xn: np.ndarray, yn: np.ndarray, intr: np.ndarray):
    """返回投影所需的中间量，投影与雅可比共用，避免重复计算。"""
    f, cx, cy, k1, k2, k3, p1, p2, b1, b2 = (intr[:, i] for i in range(10))
    r2 = xn * xn + yn * yn
    radial = 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))
    tang_x = p1 * (r2 + 2.0 * xn * xn) + 2.0 * p2 * xn * yn
    tang_y = p2 * (r2 + 2.0 * yn * yn) + 2.0 * p1 * xn * yn
    xd = xn * radial + tang_x
    yd = yn * radial + tang_y
    u = cx + (f + b1) * xd + b2 * yd
    v = cy + f * yd
    return {
        "f": f, "cx": cx, "cy": cy, "k1": k1, "k2": k2, "k3": k3,
        "p1": p1, "p2": p2, "b1": b1, "b2": b2,
        "r2": r2, "radial": radial, "xd": xd, "yd": yd, "u": u, "v": v,
    }


def _project_batch(
    rotmats: np.ndarray,
    centers: np.ndarray,
    intr_full: np.ndarray,
    points: np.ndarray,
    obs_image: np.ndarray,
    obs_camera: np.ndarray,
    obs_point: np.ndarray,
) -> tuple[np.ndarray, dict, np.ndarray]:
    """向量化投影。intr_full 为 (n_cam, 10)，列序同 INTRINSIC_NAMES。"""
    delta = points[obs_point] - centers[obs_image]
    pc = np.einsum("nij,nj->ni", rotmats[obs_image], delta)
    z = pc[:, 2]
    z = np.where(np.abs(z) < 1e-9, 1e-9, z)
    xn = pc[:, 0] / z
    yn = pc[:, 1] / z
    terms = _distortion_terms(xn, yn, intr_full[obs_camera])
    terms["xn"], terms["yn"], terms["z"] = xn, yn, z
    return np.stack([terms["u"], terms["v"]], axis=1), terms, pc


def _duv_dxy(t: dict) -> np.ndarray:
    """∂(u, v)/∂(xn, yn)，返回 (N, 2, 2)。"""
    xn, yn, r2 = t["xn"], t["yn"], t["r2"]
    a = t["k1"] + 2.0 * t["k2"] * r2 + 3.0 * t["k3"] * r2**2
    s = t["radial"]
    dxd_dxn = s + 2.0 * a * xn * xn + 6.0 * t["p1"] * xn + 2.0 * t["p2"] * yn
    dxd_dyn = 2.0 * a * xn * yn + 2.0 * t["p1"] * yn + 2.0 * t["p2"] * xn
    dyd_dxn = 2.0 * a * xn * yn + 2.0 * t["p2"] * xn + 2.0 * t["p1"] * yn
    dyd_dyn = s + 2.0 * a * yn * yn + 6.0 * t["p2"] * yn + 2.0 * t["p1"] * xn
    fb, b2, f = t["f"] + t["b1"], t["b2"], t["f"]
    out = np.empty((xn.size, 2, 2))
    out[:, 0, 0] = fb * dxd_dxn + b2 * dyd_dxn
    out[:, 0, 1] = fb * dxd_dyn + b2 * dyd_dyn
    out[:, 1, 0] = f * dyd_dxn
    out[:, 1, 1] = f * dyd_dyn
    return out


def _duv_dintrinsics(t: dict, free_idx: list[int]) -> np.ndarray:
    """∂(u, v)/∂内参，返回 (N, 2, n_free)，列序按 free_idx 取自 INTRINSIC_NAMES。"""
    xn, yn, r2 = t["xn"], t["yn"], t["r2"]
    fb, b2, f = t["f"] + t["b1"], t["b2"], t["f"]
    zero = np.zeros_like(xn)
    one = np.ones_like(xn)
    tang_x_p1 = r2 + 2.0 * xn * xn
    tang_y_p1 = 2.0 * xn * yn
    tang_x_p2 = 2.0 * xn * yn
    tang_y_p2 = r2 + 2.0 * yn * yn
    table = {
        0: (t["xd"], t["yd"]),                                     # f
        1: (one, zero),                                            # cx
        2: (zero, one),                                            # cy
        3: (fb * xn * r2 + b2 * yn * r2, f * yn * r2),             # k1
        4: (fb * xn * r2**2 + b2 * yn * r2**2, f * yn * r2**2),    # k2
        5: (fb * xn * r2**3 + b2 * yn * r2**3, f * yn * r2**3),    # k3
        6: (fb * tang_x_p1 + b2 * tang_y_p1, f * tang_y_p1),       # p1
        7: (fb * tang_x_p2 + b2 * tang_y_p2, f * tang_y_p2),       # p2
        8: (t["xd"], zero),                                        # b1
        9: (t["yd"], zero),                                        # b2
    }
    out = np.empty((xn.size, 2, len(free_idx)))
    for col, idx in enumerate(free_idx):
        du, dv = table[idx]
        out[:, 0, col] = du
        out[:, 1, col] = dv
    return out


def _intrinsic_sigma(camera: Camera) -> np.ndarray:
    """各内参先验的标准差，按 INTRINSIC_NAMES 顺序。

    取值刻意偏松，只用来排除物理上不合理的解，不左右正常收敛：
    焦距允许 5% 浮动（厂家标称 5 mm，换算后不确定度远小于此），
    主点允许 2% 像幅，径向畸变 0.25，偏心畸变 1e-3，
    仿射项 b1/b2 在正下视区块几乎不可观测，给 2.0 防止其发散。
    """
    return np.array(
        [
            0.05 * camera.f,
            0.02 * camera.width,
            0.02 * camera.height,
            0.25,
            0.25,
            0.25,
            1e-3,
            1e-3,
            2.0,
            2.0,
        ]
    )


class BAProblem:
    """把一次平差整理成 least_squares 需要的 (x0, residuals, jacobian)。

    单独成类是为了能对解析雅可比做数值微分对拍 —— 平差的雅可比一旦写错，
    表现只是「收敛得差一点」，很难从结果反推，必须有专门的测试。

    旋转用增量参数化 R = Exp(δ) R_base，δ 初值 0。这样旋转块的雅可比是
    −[pc]× J_r(δ)，解析形式干净，也避免 Rodrigues 在 |ω|→π 附近的病态。
    """

    def __init__(
        self,
        cameras: dict[str, Camera],
        poses: dict[int, Pose],
        points: np.ndarray,
        observations: list[Observation],
        image_camera: dict[int, str],
        gps: dict[int, np.ndarray],
        attitude_rotation: dict[int, np.ndarray],
        *,
        sigma_xyz: tuple[float, float, float] = (3.0, 3.0, 5.0),
        sigma_attitude_rad: float = np.radians(3.0),
        free_names: tuple[str, ...] = CALIBRATION_STAGES[-1],
        intrinsic_prior: dict[str, Camera] | None = None,
    ) -> None:
        self.image_ids = sorted(poses)
        self.img_pos = {i: k for k, i in enumerate(self.image_ids)}
        self.camera_keys = sorted(cameras)
        self.cam_pos = {k: n for n, k in enumerate(self.camera_keys)}
        self.cameras = cameras
        self.free_names = free_names
        self.free_idx = [INTRINSIC_NAMES.index(n) for n in free_names]
        self.sigma_attitude_rad = sigma_attitude_rad

        self.obs_image = np.array([self.img_pos[o.image] for o in observations], int)
        self.obs_camera = np.array(
            [self.cam_pos[image_camera[o.image]] for o in observations], int
        )
        self.obs_point = np.array([o.point for o in observations], int)
        self.obs_uv = np.array([o.uv for o in observations], float)

        # 原点平移到测区中心。UTM 的 67 万 / 262 万量级会毁掉法方程条件数。
        self.origin = np.mean([gps[i] for i in self.image_ids], axis=0)
        self.gps_local = np.array([gps[i] - self.origin for i in self.image_ids])
        # 姿态先验存成 IMU 旋转矩阵。残差取 Log(R · R_imu^T) 这个「相对」旋转，
        # 而不是 Log(R) − Log(R_imu)：正下视姿态是绕 X 轴 180°，恰好落在旋转向量
        # 参数化的奇异点上，直接对绝对姿态取 Log 会得到病态的导数。
        self.att_rot_t = np.array([attitude_rotation[i].T for i in self.image_ids])
        self.rot_base = np.array([poses[i].rotation for i in self.image_ids])
        self.fixed = np.array([cameras[k].to_vector() for k in self.camera_keys])
        self.weight_xyz = 1.0 / np.array(sigma_xyz)

        # 内参先验。焦距与径向畸变在正下视区块里高度相关：不加约束时平差可以
        # 用「f 缩小 + k 加大」拟合出同样小的残差，实测会让 f 跑偏近 20%。
        # Remote Sens. 2021, 13(21):4222 对长条带自标定给的对策就是约束内参。
        #
        # 先验必须锚在最初的相机初值上。若每一轮都拿上一轮结果当先验中心，
        # 约束就退化成棘轮：分阶段跑三轮后 f 会一路滑到 1498（真值 2100）。
        anchor = intrinsic_prior if intrinsic_prior is not None else cameras
        self.intr_prior = np.array([anchor[k].to_vector() for k in self.camera_keys])
        self.intr_sigma = np.array([_intrinsic_sigma(anchor[k]) for k in self.camera_keys])

        self.image_camera = np.array(
            [self.cam_pos[image_camera[i]] for i in self.image_ids], dtype=int
        )
        self.n_img = len(self.image_ids)
        self.n_cam = len(self.camera_keys)
        self.n_free = len(free_names)
        self.n_pts = int(points.shape[0])
        self.n_obs = len(observations)
        self.off_center = self.n_img * 3
        self.off_intr = self.n_img * 6
        self.off_point = self.off_intr + self.n_cam * self.n_free
        self.n_par = self.off_point + self.n_pts * 3
        self.n_res = self.n_obs * 2 + self.n_img * 6

        centers0 = np.array([poses[i].center - self.origin for i in self.image_ids])
        self.x0 = np.concatenate(
            [
                np.zeros(self.n_img * 3),
                centers0.ravel(),
                self.fixed[:, self.free_idx].ravel(),
                (points - self.origin).ravel(),
            ]
        )
        self.x_scale = np.concatenate(
            [
                np.full(self.n_img * 3, 0.01),
                np.full(self.n_img * 3, 1.0),
                np.tile([INTRINSIC_SCALES[n] for n in free_names], self.n_cam),
                np.full(self.n_pts * 3, 1.0),
            ]
        )

    def state(self, x: np.ndarray):
        delta = x[: self.off_center].reshape(self.n_img, 3)
        centers = x[self.off_center : self.off_intr].reshape(self.n_img, 3)
        intr = x[self.off_intr : self.off_point].reshape(self.n_cam, self.n_free)
        pts = x[self.off_point :].reshape(self.n_pts, 3)
        rotmats = Rotation.from_rotvec(delta).as_matrix() @ self.rot_base
        intr_full = self.fixed.copy()
        intr_full[:, self.free_idx] = intr
        return delta, centers, intr_full, pts, rotmats

    def residuals(self, x: np.ndarray) -> np.ndarray:
        _, centers, intr_full, pts, rotmats = self.state(x)
        uv, _, _ = _project_batch(
            rotmats, centers, intr_full, pts, self.obs_image, self.obs_camera, self.obs_point
        )
        rep = (uv - self.obs_uv).ravel()
        gps_res = ((centers - self.gps_local) * self.weight_xyz).ravel()
        # 姿态先验：当前姿态相对 IMU 观测的旋转量，量值很小，
        # 因此其对 δ 的导数取单位阵是足够精确的近似。
        att_res = (
            Rotation.from_matrix(rotmats @ self.att_rot_t).as_rotvec().ravel()
            / self.sigma_attitude_rad
        )
        return np.concatenate([rep, gps_res, att_res])

    def jacobian(self, x: np.ndarray):
        delta, centers, intr_full, pts, rotmats = self.state(x)
        _, terms, pc = _project_batch(
            rotmats, centers, intr_full, pts, self.obs_image, self.obs_camera, self.obs_point
        )
        n_obs = self.n_obs
        z = terms["z"]
        m = np.zeros((n_obs, 2, 3))
        m[:, 0, 0] = 1.0 / z
        m[:, 0, 2] = -terms["xn"] / z
        m[:, 1, 1] = 1.0 / z
        m[:, 1, 2] = -terms["yn"] / z
        g = _duv_dxy(terms) @ m  # ∂(u,v)/∂pc

        r_obs = rotmats[self.obs_image]
        jl = _left_jacobian(delta)[self.obs_image]
        blocks = [
            (g @ (-_skew(pc) @ jl), self.obs_image * 3, 3),
            (g @ (-r_obs), self.off_center + self.obs_image * 3, 3),
            (_duv_dintrinsics(terms, self.free_idx), self.off_intr + self.obs_camera * self.n_free, self.n_free),
            (g @ r_obs, self.off_point + self.obs_point * 3, 3),
        ]

        rows, cols, vals = [], [], []
        base_rows = np.arange(n_obs) * 2
        for block, col_base, width in blocks:
            col = col_base[:, None] + np.arange(width)[None, :]
            for axis in range(2):
                rows.append(np.repeat(base_rows + axis, width))
                cols.append(col.ravel())
                vals.append(block[:, axis, :].ravel())

        offset = n_obs * 2
        gi = np.arange(self.n_img * 3)
        rows.append(offset + gi)
        cols.append(self.off_center + gi)
        vals.append(np.tile(self.weight_xyz, self.n_img))

        offset += self.n_img * 3
        rows.append(offset + gi)
        cols.append(gi)
        vals.append(np.full(self.n_img * 3, 1.0 / self.sigma_attitude_rad))

        return coo_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
            shape=(self.n_res, self.n_par),
        ).tocsr()

    def unpack(self, x: np.ndarray):
        """把解向量还原成相机、位姿、物方点（回到全局 UTM 坐标）。"""
        _, centers, intr_full, pts, rotmats = self.state(x)
        out_cameras = {
            k: self.cameras[k].with_vector(intr_full[self.cam_pos[k]]) for k in self.camera_keys
        }
        out_poses = {
            i: Pose(rotmats[self.img_pos[i]], centers[self.img_pos[i]] + self.origin)
            for i in self.image_ids
        }
        return out_cameras, out_poses, pts + self.origin

    def reprojection_px(self, x: np.ndarray) -> np.ndarray:
        _, centers, intr_full, pts, rotmats = self.state(x)
        uv, _, _ = _project_batch(
            rotmats, centers, intr_full, pts, self.obs_image, self.obs_camera, self.obs_point
        )
        return np.linalg.norm(uv - self.obs_uv, axis=1)


    def linearize(self, x: np.ndarray, obs_idx: np.ndarray | None = None):
        """把问题在 x 处线性化，返回逐观测的雅可比块与残差。

        A: (n, 2, 6) 对位姿 [δ | C]
        D: (n, 2, n_free) 对内参
        B: (n, 2, 3) 对物方点
        r: (n, 2) 重投影残差（像素）

        obs_idx 给定时只线性化这一部分观测。各波段的观测互不重叠，
        分块算再拼回去与一次算完全部相同。
        """
        delta, centers, intr_full, pts, rotmats = self.state(x)
        if obs_idx is None:
            obs_image = self.obs_image
            obs_camera = self.obs_camera
            obs_point = self.obs_point
            obs_uv = self.obs_uv
        else:
            obs_image = self.obs_image[obs_idx]
            obs_camera = self.obs_camera[obs_idx]
            obs_point = self.obs_point[obs_idx]
            obs_uv = self.obs_uv[obs_idx]
        n_obs = int(obs_image.shape[0])
        if n_obs == 0:
            return (
                np.zeros((0, 2, 6)),
                np.zeros((0, 2, self.n_free)),
                np.zeros((0, 2, 3)),
                np.zeros((0, 2)),
                centers,
                rotmats,
                intr_full,
            )
        uv, terms, pc = _project_batch(
            rotmats, centers, intr_full, pts, obs_image, obs_camera, obs_point
        )
        z = terms["z"]
        m = np.zeros((n_obs, 2, 3))
        m[:, 0, 0] = 1.0 / z
        m[:, 0, 2] = -terms["xn"] / z
        m[:, 1, 1] = 1.0 / z
        m[:, 1, 2] = -terms["yn"] / z
        g = _duv_dxy(terms) @ m

        r_obs = rotmats[obs_image]
        jl = _left_jacobian(delta)[obs_image]
        a = np.concatenate([g @ (-_skew(pc) @ jl), g @ (-r_obs)], axis=2)
        d = _duv_dintrinsics(terms, self.free_idx)
        b = g @ r_obs
        return a, d, b, uv - obs_uv, centers, rotmats, intr_full

    def prior_residuals(self, centers: np.ndarray, rotmats: np.ndarray, intr_full: np.ndarray):
        """GPS 位置、IMU 姿态、内参三组先验的加权残差。

        都是高斯先验，不经过稳健核 —— 稳健核是用来对付误匹配的，先验本身没有外点。
        """
        gps_res = (centers - self.gps_local) * self.weight_xyz
        att_res = (
            Rotation.from_matrix(rotmats @ self.att_rot_t).as_rotvec() / self.sigma_attitude_rad
        )
        intr_res = (
            (intr_full[:, self.free_idx] - self.intr_prior[:, self.free_idx])
            / self.intr_sigma[:, self.free_idx]
        )
        return gps_res, att_res, intr_res


def _huber_weights(residual: np.ndarray, delta_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Huber 的 IRLS 权重与稳健代价。residual 形如 (n, 2)。"""
    s = np.linalg.norm(residual, axis=1)
    inlier = s <= delta_px
    weight = np.where(inlier, 1.0, delta_px / np.maximum(s, 1e-12))
    cost = np.where(inlier, 0.5 * s**2, delta_px * (s - 0.5 * delta_px))
    return weight, cost


@dataclass
class _SchurBand:
    """一个波段在降阶法方程里的列、观测和物方点。"""

    cam: int
    img_ids: np.ndarray
    obs_idx: np.ndarray
    point_ids: np.ndarray
    cam_cols: np.ndarray
    point_cols: np.ndarray
    n_img: int
    n_pts: int
    nc: int


def _separable_bands(problem: BAProblem) -> list[_SchurBand] | None:
    """组内选对时，各波段不共享物方点，法方程按波段块对角。

    有跨波段点时返回 None，调用方退回整块求解，避免拆开以后方程变了。
    """
    if problem.n_cam <= 1 or problem.n_obs == 0 or problem.n_free == 0:
        return None
    min_c = np.full(problem.n_pts, problem.n_cam, dtype=np.int32)
    max_c = np.full(problem.n_pts, -1, dtype=np.int32)
    np.minimum.at(min_c, problem.obs_point, problem.obs_camera)
    np.maximum.at(max_c, problem.obs_point, problem.obs_camera)
    seen = max_c >= 0
    if not np.any(seen) or np.any(min_c[seen] != max_c[seen]):
        return None
    bands: list[_SchurBand] = []
    for cam in range(problem.n_cam):
        img_ids = np.flatnonzero(problem.image_camera == cam)
        obs_idx = np.flatnonzero(problem.obs_camera == cam)
        if img_ids.size == 0:
            continue
        point_ids = np.unique(problem.obs_point[obs_idx]) if obs_idx.size else np.zeros(0, dtype=int)
        n_img = int(img_ids.size)
        n_pts = int(point_ids.size)
        local_of = np.full(problem.n_img, -1, dtype=int)
        local_of[img_ids] = np.arange(n_img)
        if obs_idx.size:
            local_img = local_of[problem.obs_image[obs_idx]]
            pose_cols = local_img[:, None] * 6 + np.arange(6)[None, :]
            intr_cols = n_img * 6 + np.arange(problem.n_free)[None, :]
            intr_cols = np.broadcast_to(intr_cols, (obs_idx.size, problem.n_free)).copy()
            cam_cols = np.concatenate([pose_cols, intr_cols], axis=1)
            point_of = np.full(problem.n_pts, -1, dtype=int)
            point_of[point_ids] = np.arange(n_pts)
            local_pts = point_of[problem.obs_point[obs_idx]]
            point_cols = local_pts[:, None] * 3 + np.arange(3)[None, :]
        else:
            cam_cols = np.zeros((0, 6 + problem.n_free), dtype=int)
            point_cols = np.zeros((0, 3), dtype=int)
        bands.append(
            _SchurBand(
                cam=cam,
                img_ids=img_ids,
                obs_idx=obs_idx,
                point_ids=point_ids,
                cam_cols=cam_cols,
                point_cols=point_cols,
                n_img=n_img,
                n_pts=n_pts,
                nc=n_img * 6 + problem.n_free,
            )
        )
    if len(bands) <= 1:
        return None
    return bands


def _assemble_band(problem: BAProblem, band: _SchurBand, x: np.ndarray, huber_px: float):
    """组出一个波段的法方程。与整块组装时该波段对应的子块相同。"""
    a, d, b, r, centers, rotmats, intr_full = problem.linearize(x, band.obs_idx)
    if r.size:
        weight, _ = _huber_weights(r, huber_px)
        sw = np.sqrt(weight)[:, None, None]
        a = a * sw
        d = d * sw
        b = b * sw
        rw = r * np.sqrt(weight)[:, None]
    else:
        b = np.zeros((0, 2, 3))
        rw = np.zeros((0, 2))
    gps_res, att_res, intr_res = problem.prior_residuals(centers, rotmats, intr_full)
    nc = band.nc
    n_obs = int(band.obs_idx.size)
    n_pts = band.n_pts
    width = 6 + problem.n_free
    if n_obs:
        jc_blocks = np.concatenate([a, d], axis=2)
        row_base = np.arange(n_obs) * 2
        rows = np.repeat(row_base, width)
        jc = coo_matrix(
            (
                np.concatenate([jc_blocks[:, 0, :].ravel(), jc_blocks[:, 1, :].ravel()]),
                (
                    np.concatenate([rows, rows + 1]),
                    np.concatenate([band.cam_cols.ravel(), band.cam_cols.ravel()]),
                ),
            ),
            shape=(n_obs * 2, nc),
        ).tocsr()
        rows3 = np.repeat(row_base, 3)
        jp = coo_matrix(
            (
                np.concatenate([b[:, 0, :].ravel(), b[:, 1, :].ravel()]),
                (
                    np.concatenate([rows3, rows3 + 1]),
                    np.concatenate([band.point_cols.ravel(), band.point_cols.ravel()]),
                ),
            ),
            shape=(n_obs * 2, max(n_pts, 1) * 3),
        ).tocsr()
        if n_pts == 0:
            jp = coo_matrix((n_obs * 2, 0))
        rvec = rw.ravel()
        u = np.asarray((jc.T @ jc).todense())
        w = (jc.T @ jp).tocsr() if n_pts else None
        g_c = -(jc.T @ rvec)
        g_p = -(jp.T @ rvec) if n_pts else np.zeros(0)
        v = np.zeros((n_pts, 3, 3))
        if n_pts:
            np.add.at(v, band.point_cols[:, 0] // 3, np.einsum("nki,nkj->nij", b, b))
    else:
        u = np.zeros((nc, nc))
        w = None
        g_c = np.zeros(nc)
        g_p = np.zeros(n_pts * 3)
        v = np.zeros((n_pts, 3, 3))

    local = np.arange(band.n_img)
    for k in range(3):
        dcols = local * 6 + k
        ccols = local * 6 + 3 + k
        u[ccols, ccols] += problem.weight_xyz[k] ** 2
        g_c[ccols] -= gps_res[band.img_ids, k] * problem.weight_xyz[k]
        u[dcols, dcols] += 1.0 / problem.sigma_attitude_rad**2
        g_c[dcols] -= att_res[band.img_ids, k] / problem.sigma_attitude_rad
    inv_sigma = 1.0 / problem.intr_sigma[band.cam, problem.free_idx]
    cols = band.n_img * 6 + np.arange(problem.n_free)
    u[cols, cols] += inv_sigma**2
    g_c[cols] -= intr_res[band.cam] * inv_sigma
    return u, w, g_c, g_p, v


def _damp_solve_band(assembled, lam: float):
    """加阻尼并做这个波段的 Schur 消元。奇异时返回 None，让外层加大 λ。"""
    u, w, g_c, g_p, v = assembled
    n_pts = v.shape[0]
    u_d = u + np.diag(lam * np.maximum(np.diag(u), 1e-9))
    if n_pts == 0:
        try:
            dc = np.linalg.solve(u_d, g_c)
        except np.linalg.LinAlgError:
            return None
        return dc, np.zeros((0, 3))
    v_d = v + lam * np.maximum(v.diagonal(axis1=1, axis2=2), 1e-9)[:, :, None] * np.eye(3)
    try:
        v_inv = np.linalg.inv(v_d)
    except np.linalg.LinAlgError:
        return None
    v_inv_sp = _block_diag_3x3(v_inv)
    y = (w @ v_inv_sp).tocsr()
    s = u_d - np.asarray((y @ w.T).todense())
    rhs = g_c - y @ g_p
    try:
        dc = np.linalg.solve(s, rhs)
    except np.linalg.LinAlgError:
        return None
    dp = np.einsum("nij,nj->ni", v_inv, (g_p - (w.T @ dc)).reshape(n_pts, 3))
    return dc, dp


def _scatter_band_step(problem: BAProblem, bands: list[_SchurBand], parts) -> tuple[np.ndarray, np.ndarray]:
    """把各波段的步长放回整幅相机参数和物方点。没观测到的点步长为 0。"""
    dc = np.zeros(problem.n_img * 6 + problem.n_cam * problem.n_free)
    dp = np.zeros((problem.n_pts, 3))
    pose = dc[: problem.n_img * 6].reshape(problem.n_img, 6)
    for band, (dc_l, dp_l) in zip(bands, parts):
        pose[band.img_ids] = dc_l[: band.n_img * 6].reshape(band.n_img, 6)
        base = problem.n_img * 6 + band.cam * problem.n_free
        dc[base : base + problem.n_free] = dc_l[band.n_img * 6 :]
        if band.n_pts:
            dp[band.point_ids] = dp_l
    return dc, dp


def solve_lm(
    problem: BAProblem,
    *,
    max_iterations: int = 40,
    huber_px: float = HUBER_SCALE_PX,
    initial_lambda: float = 1e-3,
    cost_tolerance: float = 1e-6,
    step_tolerance: float = 1e-10,
    verbose: int = 0,
    band_parallel: bool = True,
) -> np.ndarray:
    """Schur 补 Levenberg-Marquardt。

    法方程按「相机块 / 物方点块」分区：

        [ U   W ] [Δc]   [g_c]
        [ Wᵀ  V ] [Δp] = [g_p]

    V 是逐点 3x3 块对角，先消去物方点得到降阶方程
        (U − W V⁻¹ Wᵀ) Δc = g_c − W V⁻¹ g_p
    再回代求 Δp。这是 Ceres / COLMAP / g2o 的标准做法。

    组内选对之后各波段不共享物方点，降阶矩阵按波段块对角。
    每个波段单独分解，多路一起算，方程和整块求解是同一套。
    有跨波段点时仍走整块，不改方程。

    通用最小二乘（scipy least_squares + LSMR）在这个问题上收敛极慢：
    法方程条件数很差，迭代解法每步只能挪一点点。

    稳健核用 IRLS 形式的 Huber，只作用在重投影项上；GPS / IMU 先验本身
    就是高斯观测，不应再被降权。
    """
    x = problem.x0.copy()
    lam = initial_lambda
    bands = _separable_bands(problem) if band_parallel else None
    workers = ba_parallel_workers(len(bands)) if bands else 1
    pool = ThreadPoolExecutor(max_workers=workers) if bands and workers > 1 else None

    def total_cost(xv: np.ndarray) -> float:
        # 只投影，不组装雅可比。代价与线性化里的重投影残差相同。
        _, centers, intr_full, pts, rotmats = problem.state(xv)
        uv, _, _ = _project_batch(
            rotmats,
            centers,
            intr_full,
            pts,
            problem.obs_image,
            problem.obs_camera,
            problem.obs_point,
        )
        _, robust = _huber_weights(uv - problem.obs_uv, huber_px)
        gps_res, att_res, intr_res = problem.prior_residuals(centers, rotmats, intr_full)
        return float(
            robust.sum()
            + 0.5 * (gps_res**2).sum()
            + 0.5 * (att_res**2).sum()
            + 0.5 * (intr_res**2).sum()
        )

    def solve_bands(lam_now: float):
        """按封顶路数分批组装再消元。一批做完就丢掉，避免 8 路法方程同时驻留。"""
        assert bands is not None
        parts: list = []
        step = max(1, workers)
        for i in range(0, len(bands), step):
            chunk = bands[i : i + step]
            if pool is None:
                assembled = [_assemble_band(problem, band, x, huber_px) for band in chunk]
                solved = [_damp_solve_band(item, lam_now) for item in assembled]
            else:
                assembled = list(
                    pool.map(lambda band: _assemble_band(problem, band, x, huber_px), chunk)
                )
                solved = list(pool.map(lambda item: _damp_solve_band(item, lam_now), assembled))
            del assembled
            if any(part is None for part in solved):
                return None
            parts.extend(solved)
        return _scatter_band_step(problem, bands, parts)

    # 整块求解才需要全体观测的列号。分波段时每个波段自己的列号已经备好。
    if bands is None:
        nc = problem.n_img * 6 + problem.n_cam * problem.n_free
        n_pts = problem.n_pts
        pose_cols = problem.obs_image[:, None] * 6 + np.arange(6)[None, :]
        intr_cols = (
            problem.n_img * 6
            + problem.obs_camera[:, None] * problem.n_free
            + np.arange(problem.n_free)[None, :]
        )
        cam_cols = np.concatenate([pose_cols, intr_cols], axis=1)
        width = cam_cols.shape[1]
        point_cols = problem.obs_point[:, None] * 3 + np.arange(3)[None, :]
        row_base = np.arange(problem.n_obs) * 2

    def consider(dc: np.ndarray, dp: np.ndarray, iteration: int) -> str:
        nonlocal x, cost, lam
        step = np.concatenate([dc, dp.ravel()])
        x_new = _apply_step(problem, x, dc, dp)
        cost_new = total_cost(x_new)
        if cost_new < cost:
            improvement = cost - cost_new
            if verbose:
                print(
                    f"  LM {iteration:3d}  cost {cost:.6e} → {cost_new:.6e}  λ={lam:.2e}"
                )
            x = x_new
            cost = cost_new
            lam = max(lam * 0.3, 1e-10)
            if improvement < cost_tolerance * max(1.0, cost) or np.linalg.norm(step) < step_tolerance:
                return "done"
            return "next"
        lam *= 10.0
        if lam > 1e12:
            return "done"
        return "retry"

    try:
        cost = total_cost(x)
        for iteration in range(max_iterations):
            if bands is not None:
                while True:
                    solved = solve_bands(lam)
                    if solved is None:
                        lam *= 10.0
                        if lam > 1e12:
                            return x
                        continue
                    action = consider(*solved, iteration)
                    if action == "retry":
                        continue
                    if action == "done":
                        return x
                    break
                continue

            a, d, b, r, centers, rotmats, intr_full = problem.linearize(x)
            weight, _ = _huber_weights(r, huber_px)
            sw = np.sqrt(weight)[:, None, None]
            a = a * sw
            d = d * sw
            b = b * sw
            rw = r * np.sqrt(weight)[:, None]

            jc_blocks = np.concatenate([a, d], axis=2)  # (n_obs, 2, width)
            rows = np.repeat(row_base, width)
            jc = coo_matrix(
                (
                    np.concatenate([jc_blocks[:, 0, :].ravel(), jc_blocks[:, 1, :].ravel()]),
                    (
                        np.concatenate([rows, rows + 1]),
                        np.concatenate([cam_cols.ravel(), cam_cols.ravel()]),
                    ),
                ),
                shape=(problem.n_obs * 2, nc),
            ).tocsr()
            rows3 = np.repeat(row_base, 3)
            jp = coo_matrix(
                (
                    np.concatenate([b[:, 0, :].ravel(), b[:, 1, :].ravel()]),
                    (
                        np.concatenate([rows3, rows3 + 1]),
                        np.concatenate([point_cols.ravel(), point_cols.ravel()]),
                    ),
                ),
                shape=(problem.n_obs * 2, n_pts * 3),
            ).tocsr()

            rvec = rw.ravel()
            u = np.asarray((jc.T @ jc).todense())
            w = (jc.T @ jp).tocsr()
            g_c = -(jc.T @ rvec)
            g_p = -(jp.T @ rvec)

            # 先验直接累加进法方程，不经过稳健核
            gps_res, att_res, intr_res = problem.prior_residuals(centers, rotmats, intr_full)
            idx = np.arange(problem.n_img)
            for k in range(3):
                dcols = idx * 6 + k
                ccols = idx * 6 + 3 + k
                u[ccols, ccols] += problem.weight_xyz[k] ** 2
                g_c[ccols] -= gps_res[:, k] * problem.weight_xyz[k]
                u[dcols, dcols] += 1.0 / problem.sigma_attitude_rad**2
                g_c[dcols] -= att_res[:, k] / problem.sigma_attitude_rad

            inv_sigma = 1.0 / problem.intr_sigma[:, problem.free_idx]
            for c in range(problem.n_cam):
                cols = problem.n_img * 6 + c * problem.n_free + np.arange(problem.n_free)
                u[cols, cols] += inv_sigma[c] ** 2
                g_c[cols] -= intr_res[c] * inv_sigma[c]

            v = np.zeros((n_pts, 3, 3))
            np.add.at(v, problem.obs_point, np.einsum("nki,nkj->nij", b, b))

            while True:
                u_d = u + np.diag(lam * np.maximum(np.diag(u), 1e-9))
                v_d = v + lam * np.maximum(v.diagonal(axis1=1, axis2=2), 1e-9)[:, :, None] * np.eye(3)
                try:
                    v_inv = np.linalg.inv(v_d)
                except np.linalg.LinAlgError:
                    lam *= 10.0
                    continue

                v_inv_sp = _block_diag_3x3(v_inv)
                y = (w @ v_inv_sp).tocsr()
                s = u_d - np.asarray((y @ w.T).todense())
                rhs = g_c - y @ g_p
                try:
                    dc = np.linalg.solve(s, rhs)
                except np.linalg.LinAlgError:
                    lam *= 10.0
                    if lam > 1e12:
                        return x
                    continue

                dp = np.einsum("nij,nj->ni", v_inv, (g_p - (w.T @ dc)).reshape(n_pts, 3))
                action = consider(dc, dp, iteration)
                if action == "retry":
                    continue
                if action == "done":
                    return x
                break
        return x
    finally:
        if pool is not None:
            pool.shutdown(wait=True)


def _block_diag_3x3(blocks: np.ndarray):
    """(n, 3, 3) → 3n x 3n 稀疏块对角矩阵。"""
    n = blocks.shape[0]
    base = np.arange(n) * 3
    # blocks.ravel() 的顺序是 (n, i, j)，i 慢 j 快，行列索引必须与之对齐
    rows = np.repeat(base[:, None] + np.arange(3)[None, :], 3, axis=1).ravel()
    cols = np.tile(base[:, None] + np.arange(3)[None, :], (1, 3)).ravel()
    return coo_matrix((blocks.reshape(-1), (rows, cols)), shape=(n * 3, n * 3)).tocsr()


def _apply_step(problem: BAProblem, x: np.ndarray, dc: np.ndarray, dp: np.ndarray) -> np.ndarray:
    """加步长。

    注意两套布局不同：法方程的相机块按影像把 [δ(3) | C(3)] 交错排列，
    而参数向量 x 是 [全部 δ | 全部 C | 内参 | 物方点]，这里负责搬运。
    旋转不能直接相加，要按 Exp(δ_inc)·Exp(δ_old) 左乘合成。
    """
    n_img = problem.n_img
    pose_step = dc[: n_img * 6].reshape(n_img, 6)
    x_new = x.copy()
    delta_old = x[: n_img * 3].reshape(n_img, 3)
    combined = (
        Rotation.from_rotvec(pose_step[:, :3]) * Rotation.from_rotvec(delta_old)
    ).as_rotvec()
    x_new[: n_img * 3] = combined.ravel()
    x_new[problem.off_center : problem.off_intr] += pose_step[:, 3:].ravel()
    x_new[problem.off_intr : problem.off_point] += dc[n_img * 6 :]
    x_new[problem.off_point :] += dp.ravel()
    return x_new


def bundle_adjust(
    cameras: dict[str, Camera],
    poses: dict[int, Pose],
    points: np.ndarray,
    observations: list[Observation],
    image_camera: dict[int, str],
    gps: dict[int, np.ndarray],
    attitude_rotation: dict[int, np.ndarray],
    *,
    sigma_xyz: tuple[float, float, float] = (3.0, 3.0, 5.0),
    sigma_attitude_rad: float = np.radians(3.0),
    free_names: tuple[str, ...] = CALIBRATION_STAGES[-1],
    intrinsic_prior: dict[str, Camera] | None = None,
    max_iterations: int = 40,
    verbose: int = 0,
) -> tuple[dict[str, Camera], dict[int, Pose], np.ndarray, np.ndarray]:
    """一次平差。返回 (相机, 位姿, 物方点, 每观测重投影残差像素)。"""
    problem = BAProblem(
        cameras,
        poses,
        points,
        observations,
        image_camera,
        gps,
        attitude_rotation,
        sigma_xyz=sigma_xyz,
        sigma_attitude_rad=sigma_attitude_rad,
        free_names=free_names,
        intrinsic_prior=intrinsic_prior,
    )
    x = solve_lm(problem, max_iterations=max_iterations, verbose=verbose)
    out_cameras, out_poses, out_points = problem.unpack(x)
    return out_cameras, out_poses, out_points, problem.reprojection_px(x)


def drop_outliers(
    points: np.ndarray,
    observations: list[Observation],
    residual_px: np.ndarray,
    *,
    threshold_px: float = OUTLIER_THRESHOLD_PX,
    min_obs: int = MIN_OBS_PER_TRACK,
) -> tuple[np.ndarray, list[Observation], int]:
    """剔除残差超限的观测，再丢掉观测数不足的物方点，并重新编号。"""
    keep = residual_px <= threshold_px
    survivors = [o for o, k in zip(observations, keep) if k]

    counts: dict[int, int] = {}
    for o in survivors:
        counts[o.point] = counts.get(o.point, 0) + 1
    good_points = {p for p, c in counts.items() if c >= min_obs}

    remap: dict[int, int] = {}
    new_points = []
    for old in sorted(good_points):
        remap[old] = len(new_points)
        new_points.append(points[old])

    new_obs = [Observation(o.image, remap[o.point], o.uv) for o in survivors if o.point in remap]
    return np.array(new_points).reshape(-1, 3), new_obs, int((~keep).sum())


def run_at(
    cameras: dict[str, Camera],
    poses: dict[int, Pose],
    tracks: Tracks,
    features: dict[int, Features] | dict[int, np.ndarray],
    image_camera: dict[int, str],
    gps: dict[int, np.ndarray],
    attitude_rotation: dict[int, np.ndarray],
    *,
    sigma_xyz: tuple[float, float, float] = (3.0, 3.0, 5.0),
    sigma_attitude_deg: float = 3.0,
    stages: tuple[tuple[str, ...], ...] = CALIBRATION_STAGES,
    outlier_threshold_px: float = OUTLIER_THRESHOLD_PX,
    verbose: int = 0,
    log=None,
) -> ATResult:
    """完整空三：交会 → 分阶段自标定平差 → 剔粗差 → 收敛。

    ``features`` 交会后即不再需要；调用方应只传入关键点并在返回前释放，
    避免描述子与法方程同时驻留把 24 GB 引擎打满。
    """

    def _say(msg: str) -> None:
        if log is not None:
            log(msg)
        elif verbose:
            print(msg)

    # 自标定先验的锚点，整个空三过程固定不变
    intrinsic_prior = dict(cameras)
    per_image_cameras = {i: cameras[image_camera[i]] for i in poses}
    points, observations = triangulate_tracks(tracks, per_image_cameras, poses, features)
    # 交会完毕关键点不再参与 BA；清空调用方字典，给法方程腾出峰值。
    if isinstance(features, dict):
        features.clear()
    _say(f"交会  物方点 {len(points)}，观测 {len(observations)}")
    if len(points) == 0:
        raise ValueError("前方交会没有得到任何物方点，检查 POS 与匹配")

    history: list[dict] = []
    # 组内选对后各波段法方程互不相连，按波段并行分解，未知数和残差都不改。
    n_band = len({image_camera[i] for i in poses})
    if n_band > 1:
        workers = ba_parallel_workers(n_band)
        if workers < n_band:
            _say(f"平差并行下调 {n_band} → {workers}（按内存封顶）")
        else:
            _say(f"平差并行 {workers} 路")
    for stage_no, free_names in enumerate(stages, start=1):
        cameras, poses, points, residual_px = bundle_adjust(
            cameras,
            poses,
            points,
            observations,
            image_camera,
            gps,
            attitude_rotation,
            sigma_xyz=sigma_xyz,
            sigma_attitude_rad=np.radians(sigma_attitude_deg),
            free_names=free_names,
            intrinsic_prior=intrinsic_prior,
            verbose=verbose,
        )
        rms = float(np.sqrt(np.mean(residual_px**2)))
        points, observations, dropped = drop_outliers(
            points, observations, residual_px, threshold_px=outlier_threshold_px
        )
        _say(
            f"阶段{stage_no} 自由内参={','.join(free_names)}  "
            f"RMS={rms:.3f}px  剔除观测 {dropped}  剩余点 {len(points)}"
        )
        history.append(
            {"stage": stage_no, "free": list(free_names), "rms_px": rms, "dropped": dropped}
        )
        # 重新交会，让物方点跟上更新后的内外参
        per_image_cameras = {i: cameras[image_camera[i]] for i in poses}
        if len(points) == 0:
            raise ValueError("剔粗差后没有剩余物方点，阈值可能过严")

    _, _, _, residual_px = bundle_adjust(
        cameras,
        poses,
        points,
        observations,
        image_camera,
        gps,
        attitude_rotation,
        sigma_xyz=sigma_xyz,
        sigma_attitude_rad=np.radians(sigma_attitude_deg),
        free_names=stages[-1] if stages else (),
        intrinsic_prior=intrinsic_prior,
        max_iterations=1,
    )

    # 已注册 = 至少有一条重投影观测（LiMapper：无连接的影像进「未注册」）
    registered_ids = sorted({int(o.image) for o in observations})
    reg_set = set(registered_ids)
    gps_ids = [i for i in sorted(poses) if i in reg_set and i in gps]
    if gps_ids:
        deltas = np.array([poses[i].center - gps[i] for i in gps_ids])
        gps_rmse = float(np.sqrt(np.mean(np.sum(deltas**2, axis=1))))
        gps_rmse_x = float(np.sqrt(np.mean(deltas[:, 0] ** 2)))
        gps_rmse_y = float(np.sqrt(np.mean(deltas[:, 1] ** 2)))
        gps_rmse_z = float(np.sqrt(np.mean(deltas[:, 2] ** 2)))
    else:
        gps_rmse = gps_rmse_x = gps_rmse_y = gps_rmse_z = float("nan")
    n_reg = len(registered_ids)
    stats = {
        "n_images": n_reg,
        "n_poses": len(poses),
        "n_points": int(len(points)),
        "n_observations": len(observations),
        "mean_track_length": float(len(observations) / max(1, len(points))),
        "mean_obs_per_image": float(len(observations) / max(1, n_reg)),
        "rms_reprojection_px": float(np.sqrt(np.mean(residual_px**2))),
        "mean_reprojection_px": float(np.mean(residual_px)),
        "gps_rmse_m": gps_rmse,
        "gps_rmse_x_m": gps_rmse_x,
        "gps_rmse_y_m": gps_rmse_y,
        "gps_rmse_z_m": gps_rmse_z,
        "registered_ids": registered_ids,
        "n_gps": len(gps_ids),
        "stages": history,
    }
    return ATResult(cameras, poses, points, observations, residual_px, stats)
