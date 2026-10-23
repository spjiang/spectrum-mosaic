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

import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix, csr_array
from scipy.spatial.transform import Rotation

from ms_mosaic.camera import INTRINSIC_NAMES, Camera, Pose
from ms_mosaic.cpu import cpu_cap
from ms_mosaic.features import Features
from ms_mosaic.memory import (
    available_bytes,
    cap_workers,
    job_memory_gb,
    process_rss_bytes,
    resolve_memory_budget,
)
from ms_mosaic.tracks import Tracks

OUTLIER_THRESHOLD_PX = 6.0  # 与 LiMapper 报告「外点阈值（像素） 6」一致
HUBER_SCALE_PX = 1.5
MIN_TRIANGULATION_ANGLE_DEG = 1.0
MIN_OBS_PER_TRACK = 2
# 分波段平差的内存模型，系数由 scripts/calibrate_ba_memory.py 用 tracemalloc 实测拟合：
# 24 万观测/8 万点单路峰值 236 MB（4 万一块）/ 610 MB（不分块）。
# 线程各自的 malloc arena 与碎片 tracemalloc 量不到，乘 1.25 兜底。
_BA_CHUNK_OBS = 200_000
# 降阶时一次处理的观测对数；每对的中间量约 600 B，与一块雅可比同量级
_BA_PAIR_CHUNK = 200_000
_BA_BYTES_PER_CHUNK_OBS = 2100  # 一块雅可比与逐观测 JᵀJ（width=16 时）
_BA_FIXED_BYTES = 1 << 20  # einsum / nditer 缓冲等与规模无关的开销
_BA_BYTES_PER_OBS = 600  # 逐观测的 W 位姿块、W·V⁻¹ 与配对索引
_BA_BYTES_PER_POINT = 1450  # V、V⁻¹ 块对角与 g_p
_BA_DENSE_COPIES = 4  # U、U+λD/S、按影像对累加的消元块与一份转置
_BA_SAFETY = 1.25
# 不知道波段规模时的保守单路估计
_BA_WORKER_MB = 4096
# 剩余内存是实时读的，已扣掉本进程；这里只留其它容器波动与主线程代价评估的余量。
_BA_RESERVE_MB = 1536


def ba_parallel_workers(n_bands: int, per_worker_bytes: int | None = None) -> int:
    """平差并行路数：波段数 ∩ CPU ∩（任务预算 − 本进程已占 ∩ 此刻剩余内存）。

    per_worker_bytes 按最大波段的观测规模估计；不给时按 _BA_WORKER_MB 保守估计。
    """
    n = max(1, int(n_bands or 1))
    n = min(n, cpu_cap())
    budget = resolve_memory_budget(job_memory_gb())
    if budget is not None:
        budget = max(0, budget - (process_rss_bytes() or 0))
    per_mb = _BA_WORKER_MB if per_worker_bytes is None else max(64, -(-int(per_worker_bytes) // 2**20))
    return cap_workers(
        n,
        per_worker_mb=per_mb,
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


@dataclass(slots=True)
class Observation:
    """一条 (影像, 物方点, 像点) 观测。

    全波段一次有 600 万条以上，slots 省掉每条的 __dict__（约 100 B/条）。
    """

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
    # 每张影像的观测数与重投影残差平方和。observations 释放后质量报告靠它统计。
    obs_counts: dict[int, int] = field(default_factory=dict)
    obs_sq_residual: dict[int, float] = field(default_factory=dict)

    def summarize_observations(self, *, drop: bool = False) -> None:
        """把逐条观测汇总到每张影像；drop=True 时随后释放逐条观测。"""
        if self.observations and len(self.observations) == self.residuals_px.size:
            images = np.fromiter((o.image for o in self.observations), np.int64, len(self.observations))
            ids, inv = np.unique(images, return_inverse=True)
            counts = np.bincount(inv, minlength=ids.size)
            sq = np.bincount(inv, weights=np.square(self.residuals_px), minlength=ids.size)
            self.obs_counts = {int(i): int(c) for i, c in zip(ids, counts)}
            self.obs_sq_residual = {int(i): float(s) for i, s in zip(ids, sq)}
        if drop:
            self.observations = []

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
    """全部轨迹批量交会。交会角过小的点几何上不可靠，直接丢弃。

    ``features`` 可以是完整 Features，也可以只是关键点数组（交会不需要描述子）。
    方程与 ``triangulate_track`` 完全相同，仅减少描述子驻留。
    """
    keypoints = {i: _keypoints_of(feat) for i, feat in features.items()}
    usable = sorted(i for i in keypoints if i in poses and i in cameras)
    lengths = np.fromiter((len(o) for o in tracks.observations), np.int64, len(tracks.observations))
    if not usable or lengths.size == 0:
        return np.zeros((0, 3)), []
    total = int(lengths.sum())
    flat = np.fromiter((v for o in tracks.observations for pair in o for v in pair), np.int64, 2 * total)
    img_all, feat_all = flat[0::2], flat[1::2]
    track_all = np.repeat(np.arange(lengths.size), lengths)
    slot = np.full(max(max(usable), int(img_all.max(initial=0))) + 1, -1, np.int64)
    slot[usable] = np.arange(len(usable))
    keep_obs = slot[img_all] >= 0
    img, feat, track = img_all[keep_obs], feat_all[keep_obs], track_all[keep_obs]
    n_obs = np.bincount(track, minlength=lengths.size)
    starts = np.concatenate([[0], np.cumsum(n_obs)[:-1]])

    # 关键点拼成一张大表按偏移取像点；float32→float64 与逐条 np.array(..., dtype=float) 相同
    s = slot[img]
    uv = np.empty((img.size, 2))
    for k, i in enumerate(usable):
        sel = s == k
        uv[sel] = np.asarray(keypoints[i])[feat[sel], :2]

    # 去畸变是逐元素定点迭代，按相机分批与逐点调用结果相同
    xn = np.empty(img.size)
    yn = np.empty(img.size)
    cam_of = {}
    for k, i in enumerate(usable):
        cam_of.setdefault(id(cameras[i]), (cameras[i], []))[1].append(k)
    for cam, ks in cam_of.values():
        sel = np.isin(s, ks)
        xn[sel], yn[sel] = cam.undistort_pixels(uv[sel, 0], uv[sel, 1])

    proj = np.stack([_projection_matrix(poses[i]) for i in usable])
    rot = np.stack([poses[i].rotation for i in usable])
    ctr = np.stack([np.asarray(poses[i].center, float) for i in usable])

    pts = np.full((lengths.size, 3), np.nan)
    ok = np.zeros(lengths.size, bool)
    # 按观测数分组批量 DLT（Hartley & Zisserman 2004, §12.2）；每组内与逐条 SVD 同一 LAPACK 例程
    chunk = 200_000
    for k in np.unique(n_obs[n_obs >= MIN_OBS_PER_TRACK]):
        tids_all = np.flatnonzero(n_obs == k)
        for c0 in range(0, tids_all.size, chunk):
            tids = tids_all[c0 : c0 + chunk]
            idx = starts[tids][:, None] + np.arange(k)[None, :]
            sk = s[idx]
            p = proj[sk]
            x = xn[idx][..., None]
            y = yn[idx][..., None]
            a = np.empty((tids.size, 2 * k, 4))
            a[:, 0::2] = x * p[:, :, 2] - p[:, :, 0]
            a[:, 1::2] = y * p[:, :, 2] - p[:, :, 1]
            h = np.linalg.svd(a)[2][:, -1]
            good = np.abs(h[:, 3]) >= 1e-12
            point = np.full((tids.size, 3), np.nan)
            point[good] = h[good, :3] / h[good, 3:4]
            # 最大交会角 = 最小夹角余弦的反余弦（arccos 单调递减）
            v = np.stack([xn[idx], yn[idx], np.ones((tids.size, k))], axis=-1)
            d = np.einsum("nkji,nkj->nki", rot[sk], v)
            d /= np.linalg.norm(d, axis=-1, keepdims=True)
            cos = np.clip(np.einsum("nai,nbi->nab", d, d), -1.0, 1.0)
            iu = np.triu_indices(k, 1)
            angle = np.degrees(np.arccos(cos[:, iu[0], iu[1]].min(axis=1)))
            angle[~good] = 0.0
            # 交会点必须在所有相机前方
            depth = np.einsum("nkj,nkj->nk", point[:, None, :] - ctr[sk], rot[sk][:, :, 2, :])
            accept = np.all(np.isfinite(point), axis=1) & (angle >= min_angle_deg) & np.all(depth > 0, axis=1)
            pts[tids] = point
            ok[tids] = accept

    kept = np.flatnonzero(ok)
    observations: list[Observation] = []
    for point_id, t in enumerate(kept):
        b = int(starts[t])
        for j in range(b, b + int(n_obs[t])):
            observations.append(Observation(int(img[j]), point_id, uv[j]))
    return (pts[kept].reshape(-1, 3), observations)


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
    """一个波段在降阶法方程里的影像、观测和物方点。

    obs_img / obs_pt 是各观测在波段内的影像号与点号。pair_a / pair_b 是同一物方点上
    两两不同的观测（a 在 b 前），按影像对 pair_key = img_a·n_img + img_b 排好序，
    消元时据此把 W V⁻¹ Wᵀ 逐个 6x6 块累加到影像对上。
    """

    cam: int
    img_ids: np.ndarray
    obs_idx: np.ndarray
    point_ids: np.ndarray
    obs_img: np.ndarray
    obs_pt: np.ndarray
    pair_a: np.ndarray
    pair_b: np.ndarray
    pair_key: np.ndarray
    n_img: int
    n_pts: int
    nc: int


def _track_pairs(obs_img: np.ndarray, obs_pt: np.ndarray, n_img: int):
    """同一物方点上的观测两两配对（a 在 b 前，不含自身），按影像对排序。

    W V⁻¹ Wᵀ 在相机块 (i, k) 上的值是看到同一点的观测对之和
    （Triggs et al. 2000 §6.1；Ceres SchurEliminator），结构只与轨迹有关，算一次即可。
    """
    n = int(obs_pt.size)
    empty = np.zeros(0, dtype=np.intp)
    if n < 2:
        return empty, empty, empty.astype(np.int64)
    order = np.argsort(obs_pt, kind="stable")
    pt_sorted = obs_pt[order]
    ends = np.append(np.flatnonzero(np.diff(pt_sorted)) + 1, n)
    group_end = np.repeat(ends, np.diff(np.concatenate([[0], ends])))
    after = group_end - np.arange(n) - 1
    total = int(after.sum())
    if total == 0:
        return empty, empty, empty.astype(np.int64)
    first = np.repeat(np.arange(n), after)
    offset = np.arange(total) - np.repeat(np.cumsum(after) - after, after)
    a = order[first]
    b = order[first + 1 + offset]
    key = obs_img[a].astype(np.int64) * n_img + obs_img[b]
    perm = np.argsort(key, kind="stable")
    return a[perm], b[perm], key[perm]


def _group_sum(index: np.ndarray, values: np.ndarray, n: int) -> np.ndarray:
    """values 的首维按 index 分组求和到 n 组。

    用稀疏指示矩阵乘稠密阵：一遍顺序扫描、释放 GIL，比 np.add.at 的逐元素路径快得多。
    """
    m = int(index.shape[0])
    if m == 0:
        return np.zeros((n,) + values.shape[1:])
    flat = np.ascontiguousarray(values).reshape(m, -1)
    indicator = csr_array((np.ones(m), (index, np.arange(m))), shape=(n, m))
    return (indicator @ flat).reshape((n,) + values.shape[1:])


def _separable_bands(problem: BAProblem) -> list[_SchurBand] | None:
    """组内选对时，各波段不共享物方点，法方程按波段块对角。

    有跨波段点时返回 None，调用方退回整块求解，避免拆开以后方程变了。
    """
    if problem.n_obs == 0 or problem.n_free == 0:
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
        local_of = np.full(problem.n_img, -1, dtype=np.intp)
        local_of[img_ids] = np.arange(n_img)
        point_of = np.full(problem.n_pts, -1, dtype=np.intp)
        point_of[point_ids] = np.arange(n_pts)
        obs_img = local_of[problem.obs_image[obs_idx]]
        obs_pt = point_of[problem.obs_point[obs_idx]]
        pair_a, pair_b, pair_key = _track_pairs(obs_img, obs_pt, n_img)
        bands.append(
            _SchurBand(
                cam=cam,
                img_ids=img_ids,
                obs_idx=obs_idx,
                point_ids=point_ids,
                obs_img=obs_img,
                obs_pt=obs_pt,
                pair_a=pair_a,
                pair_b=pair_b,
                pair_key=pair_key,
                n_img=n_img,
                n_pts=n_pts,
                nc=n_img * 6 + problem.n_free,
            )
        )
    # 单相机也走分块组装路径（一个块），峰值同样受分块控制
    return bands or None


def band_worker_bytes(n_obs: int, n_pts: int, nc: int, n_free: int, *, chunk: int = _BA_CHUNK_OBS) -> int:
    """一路波段平差（组装 + Schur 消元）的峰值内存估计，系数用 tracemalloc 实测标定。

    组装按观测分块，雅可比只驻留一块；跨块累加的 W、消元时的 Y=W·V⁻¹
    与观测数成正比；降阶矩阵 S 及其副本是 nc² 稠密阵。
    """
    width = 6 + int(n_free)
    per_chunk_obs = min(int(n_obs), int(chunk))
    raw = (
        _BA_FIXED_BYTES
        + per_chunk_obs * _BA_BYTES_PER_CHUNK_OBS * width / 16
        + int(n_obs) * _BA_BYTES_PER_OBS
        + int(n_pts) * _BA_BYTES_PER_POINT
        + int(nc) * int(nc) * 8 * _BA_DENSE_COPIES
    )
    return int(raw * _BA_SAFETY)


def _assemble_band(
    problem: BAProblem,
    band: _SchurBand,
    x: np.ndarray,
    huber_px: float,
    *,
    chunk: int = _BA_CHUNK_OBS,
):
    """组出一个波段的法方程。与整块组装时该波段对应的子块相同。

    每个观测只连一张影像的位姿、本波段内参和一个物方点，所以：
    U 的位姿部分是逐影像 6x6 块对角，加上位姿-内参与内参-内参两条；
    W 拆成逐观测的 6x3 位姿块与逐点的 n_free x 3 内参块。
    这些块直接按观测算出再分组求和，不经过通用稀疏矩阵乘。

    JᵀJ、Jᵀr 对观测可加，按块线性化再累加，结果与一次组装相同，
    但雅可比（每观测约 2 KB）只驻留一块，峰值不再随波段观测数增长。
    """
    _, centers, intr_full, _, rotmats = problem.state(x)
    gps_res, att_res, intr_res = problem.prior_residuals(centers, rotmats, intr_full)
    nc = band.nc
    n_obs = int(band.obs_idx.size)
    n_pts = band.n_pts
    n_img = band.n_img
    n_free = problem.n_free
    width = 6 + n_free
    img_h = np.zeros((n_img, width, width))
    img_g = np.zeros((n_img, width))
    w_pose = np.empty((n_obs, 6, 3))
    w_intr = np.zeros((n_pts, n_free, 3))
    g_p = np.zeros((n_pts, 3))
    v = np.zeros((n_pts, 3, 3))
    step = max(1, int(chunk))
    for start in range(0, n_obs, step):
        sl = slice(start, start + step)
        a, d, b, r, _, _, _ = problem.linearize(x, band.obs_idx[sl])
        weight, _ = _huber_weights(r, huber_px)
        sw = np.sqrt(weight)
        jc = np.concatenate([a, d], axis=2) * sw[:, None, None]
        del a, d
        b = b * sw[:, None, None]
        r = r * sw[:, None]
        del weight, sw
        img = band.obs_img[sl]
        pt = band.obs_pt[sl]
        img_h += _group_sum(img, np.einsum("nki,nkj->nij", jc, jc), n_img)
        img_g += _group_sum(img, np.einsum("nki,nk->ni", jc, r), n_img)
        jb = np.einsum("nki,nkj->nij", jc, b)
        w_pose[sl] = jb[:, :6]
        w_intr += _group_sum(pt, jb[:, 6:], n_pts)
        g_p += _group_sum(pt, np.einsum("nki,nk->ni", b, r), n_pts)
        v += _group_sum(pt, np.einsum("nki,nkj->nij", b, b), n_pts)
        del jc, b, r, jb

    n_pose = n_img * 6
    local = np.arange(n_img)
    u = np.zeros((nc, nc))
    pose_pose = np.zeros((n_img, 6, n_img, 6))
    pose_pose[local, :, local, :] = img_h[:, :6, :6]
    u[:n_pose, :n_pose] = pose_pose.reshape(n_pose, n_pose)
    del pose_pose
    pose_intr = img_h[:, :6, 6:].reshape(n_pose, n_free)
    u[:n_pose, n_pose:] = pose_intr
    u[n_pose:, :n_pose] = pose_intr.T
    u[n_pose:, n_pose:] = img_h[:, 6:, 6:].sum(axis=0)
    g_c = -np.concatenate([img_g[:, :6].ravel(), img_g[:, 6:].sum(axis=0)])
    g_p = -g_p.ravel()

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
    return _BandNormal(u=u, w_pose=w_pose, w_intr=w_intr, g_c=g_c, g_p=g_p, v=v, band=band)


@dataclass
class _BandNormal:
    """一个波段的法方程。W 不存成稀疏阵，而是按结构拆开：

    w_pose[o]：第 o 个观测所在影像的位姿与其物方点之间的 6x3 块；
    w_intr[j]：本波段内参与第 j 个物方点之间的 n_free x 3 块。
    """

    u: np.ndarray
    w_pose: np.ndarray
    w_intr: np.ndarray
    g_c: np.ndarray
    g_p: np.ndarray
    v: np.ndarray
    band: _SchurBand


def _pair_schur(z_pose: np.ndarray, w_pose: np.ndarray, band: _SchurBand, *, chunk: int) -> np.ndarray:
    """同一物方点上不同观测 (a, b) 的 Z_a W_bᵀ（Z = W V⁻¹），按影像对累加，含 (b, a) 的转置项。

    返回 (n_img, n_img, 6, 6)。配对已按影像对排序，一块内用 reduceat 合并相同影像对，
    合并后影像对互不相同，直接按下标累加。
    """
    n = band.n_img
    acc = np.zeros((n * n, 6, 6))
    for start in range(0, int(band.pair_key.size), max(1, int(chunk))):
        sl = slice(start, start + chunk)
        key = band.pair_key[sl]
        blocks = np.matmul(z_pose[band.pair_a[sl]], w_pose[band.pair_b[sl]].transpose(0, 2, 1))
        head = np.flatnonzero(np.concatenate([[True], key[1:] != key[:-1]]))
        acc[key[head]] += np.add.reduceat(blocks, head, axis=0)
        del blocks
    acc = acc.reshape(n, n, 6, 6)
    return acc + acc.transpose(1, 0, 3, 2)


def _damp_solve_band(normal: _BandNormal, lam: float, *, chunk: int = _BA_CHUNK_OBS):
    """加阻尼并做这个波段的 Schur 消元。奇异时返回 None，让外层加大 λ。

    S = U − W V⁻¹ Wᵀ 按块直接算：位姿-位姿块来自同一观测（对角）与同点观测对；
    位姿-内参、内参-内参来自逐点内参块。数学上与稀疏阵相乘相同，只是求和次序不同。
    """
    band = normal.band
    n_pts = band.n_pts
    n_img = band.n_img
    n_obs = int(band.obs_idx.size)
    n_free = normal.w_intr.shape[1]
    n_pose = n_img * 6
    u_d = normal.u + np.diag(lam * np.maximum(np.diag(normal.u), 1e-9))
    if n_pts == 0:
        try:
            dc = np.linalg.solve(u_d, normal.g_c)
        except np.linalg.LinAlgError:
            return None
        return dc, np.zeros((0, 3))
    v = normal.v
    v_d = v + lam * np.maximum(v.diagonal(axis1=1, axis2=2), 1e-9)[:, :, None] * np.eye(3)
    try:
        v_inv = np.linalg.inv(v_d)
    except np.linalg.LinAlgError:
        return None
    w_pose = normal.w_pose
    w_intr = normal.w_intr
    g_p = normal.g_p.reshape(n_pts, 3)
    q = np.einsum("nij,nj->ni", v_inv, g_p)
    z_intr = np.matmul(w_intr, v_inv)

    z_pose = np.empty_like(w_pose)
    same_obs = np.zeros((n_img, 6, 6))
    pose_intr = np.zeros((n_img, 6, n_free))
    yg_pose = np.zeros((n_img, 6))
    step = max(1, int(chunk))
    for start in range(0, n_obs, step):
        sl = slice(start, start + step)
        img = band.obs_img[sl]
        pt = band.obs_pt[sl]
        wp = w_pose[sl]
        zp = np.matmul(wp, v_inv[pt])
        z_pose[sl] = zp
        same_obs += _group_sum(img, np.matmul(zp, wp.transpose(0, 2, 1)), n_img)
        pose_intr += _group_sum(img, np.matmul(zp, w_intr[pt].transpose(0, 2, 1)), n_img)
        yg_pose += _group_sum(img, np.einsum("nij,nj->ni", wp, q[pt]), n_img)
        del zp

    pose_pose = _pair_schur(z_pose, w_pose, band, chunk=_BA_PAIR_CHUNK)
    del z_pose
    local = np.arange(n_img)
    pose_pose[local, local] += same_obs
    s = u_d
    s[:n_pose, :n_pose] -= pose_pose.transpose(0, 2, 1, 3).reshape(n_pose, n_pose)
    del pose_pose
    cross = pose_intr.reshape(n_pose, n_free)
    s[:n_pose, n_pose:] -= cross
    s[n_pose:, :n_pose] -= cross.T
    s[n_pose:, n_pose:] -= np.einsum("nik,njk->ij", z_intr, w_intr)
    rhs = normal.g_c - np.concatenate([yg_pose.ravel(), np.einsum("nij,nj->i", w_intr, q)])
    try:
        dc = np.linalg.solve(s, rhs)
    except np.linalg.LinAlgError:
        return None

    dc_pose = dc[:n_pose].reshape(n_img, 6)
    wt_dc = np.einsum("nij,i->nj", w_intr, dc[n_pose:])
    for start in range(0, n_obs, step):
        sl = slice(start, start + step)
        contrib = np.einsum("nij,ni->nj", w_pose[sl], dc_pose[band.obs_img[sl]])
        wt_dc += _group_sum(band.obs_pt[sl], contrib, n_pts)
    dp = np.einsum("nij,nj->ni", v_inv, g_p - wt_dc)
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
    log=None,
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
    workers = 1
    if bands:
        per_worker = max(
            band_worker_bytes(int(b.obs_idx.size), b.n_pts, b.nc, problem.n_free) for b in bands
        )
        workers = ba_parallel_workers(len(bands), per_worker)
        if log is not None:
            avail = available_bytes()
            log(
                f"平差并行 {workers}/{len(bands)} 路（单路约 {per_worker / 2**30:.1f} GB，"
                f"此刻剩余 {avail / 2**30:.1f} GB）" if avail is not None
                else f"平差并行 {workers}/{len(bands)} 路（单路约 {per_worker / 2**30:.1f} GB）"
            )
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

    cached: dict = {"x": None, "assembled": None}

    def solve_bands(lam_now: float):
        """按封顶路数分批组装再消元。一批做完就丢掉，避免 8 路法方程同时驻留。

        法方程只依赖 x、不依赖 λ。全部波段能同时驻留时（单波段或路数够），
        步长被拒后加大 λ 只重做消元，不重新组装。
        """
        assert bands is not None
        parts: list = []
        step = max(1, workers)
        if step >= len(bands):
            if cached["x"] is not x:
                cached["assembled"] = None
                if pool is None:
                    cached["assembled"] = [_assemble_band(problem, b, x, huber_px) for b in bands]
                else:
                    cached["assembled"] = list(
                        pool.map(lambda b: _assemble_band(problem, b, x, huber_px), bands)
                    )
                cached["x"] = x
            items = cached["assembled"]
            if pool is None:
                solved = [_damp_solve_band(item, lam_now) for item in items]
            else:
                solved = list(pool.map(lambda item: _damp_solve_band(item, lam_now), items))
            if any(part is None for part in solved):
                return None
            return _scatter_band_step(problem, bands, solved)
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
    log=None,
) -> tuple[dict[str, Camera], dict[int, Pose], np.ndarray, np.ndarray]:
    """一次平差。返回 (相机, 位姿, 物方点, 每观测重投影残差像素)。

    组内选对时各波段不共享物方点、相机各自独立，目标函数是各波段之和、
    互不耦合。此时每个波段单独跑一套 LM（各自的 λ 与收敛判据），按内存封顶
    并行。共用一个 λ 时任一波段步长被拒就要 8 个波段一起重组装重试，阶段 2
    实测因此从 23 min 拖到 2 h 12 min。
    """
    groups = _camera_groups(points, observations, image_camera, poses)
    if groups is not None and len(groups) > 1:
        return _bundle_adjust_by_camera(
            groups,
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
            max_iterations=max_iterations,
            verbose=verbose,
            log=log,
        )
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
    x = solve_lm(problem, max_iterations=max_iterations, verbose=verbose, log=log)
    out_cameras, out_poses, out_points = problem.unpack(x)
    return out_cameras, out_poses, out_points, problem.reprojection_px(x)


@dataclass
class _CameraGroup:
    key: str
    image_ids: list[int]
    point_ids: np.ndarray  # 全局物方点号
    obs_idx: np.ndarray  # 全局观测下标


def _camera_groups(
    points: np.ndarray,
    observations: list[Observation],
    image_camera: dict[int, str],
    poses: dict[int, Pose],
) -> list[_CameraGroup] | None:
    """按相机把观测分组；有物方点跨相机时返回 None（方程耦合，不能拆）。"""
    if not observations:
        return None
    keys = sorted({image_camera[i] for i in poses})
    if len(keys) <= 1:
        return None
    key_pos = {k: n for n, k in enumerate(keys)}
    n = len(observations)
    obs_cam = np.fromiter((key_pos[image_camera[o.image]] for o in observations), np.int32, n)
    obs_pt = np.fromiter((o.point for o in observations), np.int64, n)
    n_pts = int(points.shape[0])
    lo = np.full(n_pts, len(keys), np.int32)
    hi = np.full(n_pts, -1, np.int32)
    np.minimum.at(lo, obs_pt, obs_cam)
    np.maximum.at(hi, obs_pt, obs_cam)
    seen = hi >= 0
    if np.any(lo[seen] != hi[seen]):
        return None
    groups = []
    for k, key in enumerate(keys):
        idx = np.flatnonzero(obs_cam == k)
        imgs = sorted(i for i in poses if image_camera[i] == key)
        if not imgs:
            continue
        groups.append(_CameraGroup(key, imgs, np.unique(obs_pt[idx]), idx))
    return groups


def _ba_process_init() -> None:
    """平差子进程：每路已是进程，BLAS 再开线程会超订。"""
    import os

    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(var, "1")


def _solve_camera_group(job: dict):
    """一个波段的独立 LM。顶层函数才能被 spawn 进程导入。"""
    g: _CameraGroup = job["group"]
    local_of = np.full(int(job["n_global_pts"]), -1, np.int64)
    local_of[g.point_ids] = np.arange(g.point_ids.size)
    sub_obs = [
        Observation(o.image, int(local_of[o.point]), o.uv) for o in job["observations"]
    ]
    problem = BAProblem(
        {g.key: job["camera"]},
        job["poses"],
        job["points"],
        sub_obs,
        {i: g.key for i in g.image_ids},
        job["gps"],
        job["att"],
        sigma_xyz=job["sigma_xyz"],
        sigma_attitude_rad=job["sigma_attitude_rad"],
        free_names=job["free_names"],
        intrinsic_prior=job["prior"],
    )
    del sub_obs
    x = solve_lm(problem, max_iterations=job["max_iterations"], verbose=job["verbose"])
    cams, sub_out, pts = problem.unpack(x)
    return g, cams, sub_out, pts, problem.reprojection_px(x)


def _bundle_adjust_by_camera(
    groups: list[_CameraGroup],
    cameras: dict[str, Camera],
    poses: dict[int, Pose],
    points: np.ndarray,
    observations: list[Observation],
    image_camera: dict[int, str],
    gps: dict[int, np.ndarray],
    attitude_rotation: dict[int, np.ndarray],
    *,
    sigma_xyz,
    sigma_attitude_rad,
    free_names,
    intrinsic_prior,
    max_iterations,
    verbose,
    log,
):
    n_free = len(free_names)
    per_worker = max(
        band_worker_bytes(int(g.obs_idx.size), int(g.point_ids.size), len(g.image_ids) * 6 + n_free, n_free)
        for g in groups
    )
    workers = ba_parallel_workers(len(groups), per_worker)
    if log is not None:
        avail = available_bytes()
        tail = f"，此刻剩余 {avail / 2**30:.1f} GB" if avail is not None else ""
        kind = "进程" if workers > 1 else "路"
        log(f"平差按波段独立求解 {workers}/{len(groups)} {kind}（单路约 {per_worker / 2**30:.1f} GB{tail}）")

    n_global = int(points.shape[0])
    jobs = []
    for g in groups:
        jobs.append(
            {
                "group": g,
                "n_global_pts": n_global,
                "camera": cameras[g.key],
                "poses": {i: poses[i] for i in g.image_ids},
                "points": points[g.point_ids],
                "observations": [observations[int(k)] for k in g.obs_idx],
                "gps": {i: gps[i] for i in g.image_ids},
                "att": {i: attitude_rotation[i] for i in g.image_ids},
                "sigma_xyz": sigma_xyz,
                "sigma_attitude_rad": sigma_attitude_rad,
                "free_names": free_names,
                "prior": {g.key: (intrinsic_prior or cameras)[g.key]},
                "max_iterations": max_iterations,
                "verbose": verbose,
            }
        )

    if workers > 1:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=ctx, initializer=_ba_process_init
        ) as pool:
            results = list(pool.map(_solve_camera_group, jobs))
    else:
        results = [_solve_camera_group(job) for job in jobs]

    out_cameras = dict(cameras)
    out_poses = dict(poses)
    out_points = np.array(points, copy=True)
    residual = np.zeros(len(observations))
    for g, cams, sub_out, pts, res in results:
        out_cameras.update(cams)
        out_poses.update(sub_out)
        out_points[g.point_ids] = pts
        residual[g.obs_idx] = res
    return out_cameras, out_poses, out_points, residual


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
    # 并行路数在每次平差开始时按实时剩余内存与波段规模重算。
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
            log=_say,
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

    # 剔粗差后的残差直接在当前参数处重投影；不再多跑一轮 LM（那一轮的解会被丢弃，残差与返回位姿对不上）。
    final_problem = BAProblem(
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
    )
    residual_px = final_problem.reprojection_px(final_problem.x0)
    del final_problem

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
