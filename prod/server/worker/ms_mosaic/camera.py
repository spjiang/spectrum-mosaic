"""相机模型与位姿。

内参用 Brown-Conrady 径向 + 偏心畸变，外加 b1/b2 仿射项，参数化与 Agisoft
Metashape / LiMapper 报告的 ``f, cx, cy, k1, k2, k3, p1, p2, b1, b2`` 一一对应，
这样自标定结果可以直接填进质量报告的相机标定表。

坐标系约定
----------
世界系 ENU：X 东、Y 北、Z 天，单位米（UTM + 椭球高）。
相机系 CV  ：x 右、y 下、z 光轴指向景物。
姿态 yaw/pitch/roll 取 XMP 的航空惯例：yaw 自北顺时针，pitch = -90 为正下视。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

# 自标定初值。LiMapper 报告里 MAX-S800 的 f/宽 在 1.047(RGB) ~ 1.22(MS) 之间，
# 本批 S810 宽 2048，按 AGL 111.8 m 与商业正射 GSD 0.0539 m 反算 f ≈ 2074 px，
# 与 RGB 比值外推的 2145 px 同量级，取 2100 作初值，由平差收敛。
FOCAL_OVER_WIDTH_RGB = 1.047
FOCAL_OVER_WIDTH_MS = 1.104

# 相机系 (x右, y下, z前) 相对机体系 (前, 右, 下) 的固定旋转。
_R_CAM_FROM_BODY = np.array(
    [
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
    ]
)

# NED (北, 东, 地) 相对 ENU (东, 北, 天)。
_R_NED_FROM_ENU = np.array(
    [
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
    ]
)

# 自标定参数顺序，与平差里的参数向量布局一致。
INTRINSIC_NAMES = ("f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2")


def _rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]])


def _ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])


def wrap_yaw_deg(yaw_deg: float) -> float:
    """把 XMP 里出现的 -516.8 之类的越界航向归一化到 [-180, 180)。"""
    return (float(yaw_deg) + 180.0) % 360.0 - 180.0


def rotation_from_ypr(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """由航向/俯仰/横滚构造世界(ENU) → 相机的旋转矩阵。

    正下视、航向朝北时返回 diag(1, -1, -1)：像素 x 向东，像素 y 向南，光轴朝下。
    """
    yaw = math.radians(wrap_yaw_deg(yaw_deg))
    pitch = math.radians(float(pitch_deg))
    roll = math.radians(float(roll_deg))
    r_body_from_ned = _rx(roll) @ _ry(pitch) @ _rz(yaw)
    return _R_CAM_FROM_BODY @ r_body_from_ned @ _R_NED_FROM_ENU


def ypr_from_rotation(r_cam_from_enu: np.ndarray) -> tuple[float, float, float]:
    """rotation_from_ypr 的逆运算，返回 (yaw, pitch, roll) 角度。"""
    r_body_from_ned = _R_CAM_FROM_BODY.T @ np.asarray(r_cam_from_enu, float) @ _R_NED_FROM_ENU.T
    # r_body_from_ned = Rx(roll) Ry(pitch) Rz(yaw)，其 [0,2] 元素为 -sin(pitch)
    pitch = math.asin(max(-1.0, min(1.0, -r_body_from_ned[0, 2])))
    yaw = math.atan2(r_body_from_ned[0, 1], r_body_from_ned[0, 0])
    roll = math.atan2(r_body_from_ned[1, 2], r_body_from_ned[2, 2])
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


@dataclass(frozen=True)
class Camera:
    """一个波段对应一台相机，内参独立自标定。"""

    key: str
    width: int
    height: int
    f: float
    cx: float
    cy: float
    k1: float = 0.0
    k2: float = 0.0
    k3: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    b1: float = 0.0
    b2: float = 0.0
    # 与本测区 LiMapper 报告相机类型一致（MAX-S800）
    model: str = "MAX-S800"

    @classmethod
    def initial(cls, key: str, width: int, height: int, *, kind: str = "ms") -> Camera:
        ratio = FOCAL_OVER_WIDTH_RGB if kind == "rgb" else FOCAL_OVER_WIDTH_MS
        return cls(
            key=key,
            width=int(width),
            height=int(height),
            f=ratio * float(width),
            cx=float(width) / 2.0,
            cy=float(height) / 2.0,
        )

    def to_vector(self) -> np.ndarray:
        return np.array([getattr(self, n) for n in INTRINSIC_NAMES], float)

    def with_vector(self, vec: np.ndarray) -> Camera:
        return replace(self, **{n: float(v) for n, v in zip(INTRINSIC_NAMES, vec)})

    def distort(self, xn: np.ndarray, yn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """归一化像平面坐标 → 加畸变后的归一化坐标。"""
        r2 = xn * xn + yn * yn
        radial = 1.0 + r2 * (self.k1 + r2 * (self.k2 + r2 * self.k3))
        xd = xn * radial + (self.p1 * (r2 + 2.0 * xn * xn) + 2.0 * self.p2 * xn * yn)
        yd = yn * radial + (self.p2 * (r2 + 2.0 * yn * yn) + 2.0 * self.p1 * xn * yn)
        return xd, yd

    def to_pixels(self, xd: np.ndarray, yd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        u = self.cx + self.f * xd + self.b1 * xd + self.b2 * yd
        v = self.cy + self.f * yd
        return u, v

    def project_normalized(self, xn: np.ndarray, yn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        xd, yd = self.distort(xn, yn)
        return self.to_pixels(xd, yd)

    def undistort_pixels(
        self, u: np.ndarray, v: np.ndarray, *, iterations: int = 12
    ) -> tuple[np.ndarray, np.ndarray]:
        """像素 → 去畸变的归一化坐标，定点迭代求逆。"""
        u = np.asarray(u, float)
        v = np.asarray(v, float)
        yd = (v - self.cy) / self.f
        xd = (u - self.cx - self.b2 * yd) / (self.f + self.b1)
        xn, yn = xd.copy(), yd.copy()
        for _ in range(iterations):
            xp, yp = self.distort(xn, yn)
            xn += xd - xp
            yn += yd - yp
        return xn, yn

    def format_normalized_radius(self) -> float:
        """像幅四角的无畸变半径，再留 15% 给桶形畸变。

        Brown–Conrady 只在像幅内单调（Brown, PE&RS 1966）。本测区标定
        k1/k2/k3 在归一化半径约 1.88 处回折，而像幅四角只有 0.54。回折会把
        几百米外的地面折回像幅中央，``in_bounds`` 仍判在幅内，真正射就把
        树冠拉成油彩条。四角半径的 1.15 倍仍远在回折之前，角点本身留得下。
        """
        focal = max(abs(float(self.f)), 1e-6)
        du = max(float(self.cx), float(self.width - 1) - float(self.cx)) / focal
        dv = max(float(self.cy), float(self.height - 1) - float(self.cy)) / focal
        return float(np.hypot(du, dv) * 1.15)

    def in_bounds(self, u: np.ndarray, v: np.ndarray, *, margin: float = 0.0) -> np.ndarray:
        return (
            (u >= margin)
            & (u <= self.width - 1 - margin)
            & (v >= margin)
            & (v <= self.height - 1 - margin)
        )


@dataclass
class Pose:
    """一次曝光某台相机的外方位：世界→相机旋转，以及相机中心（世界系，米）。"""

    rotation: np.ndarray
    center: np.ndarray

    @classmethod
    def from_ypr(
        cls, center: np.ndarray, yaw_deg: float, pitch_deg: float, roll_deg: float
    ) -> Pose:
        return cls(rotation_from_ypr(yaw_deg, pitch_deg, roll_deg), np.asarray(center, float))

    @property
    def viewing_direction(self) -> np.ndarray:
        """光轴在世界系的单位向量（第三行的转置）。"""
        return self.rotation[2, :].copy()

    def world_to_camera(self, pts: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        # 用 einsum 而不是 @：Apple Accelerate 的 BLAS 在大矩阵乘时会误置浮点异常
        # 标志位，刷出一堆虚假的 overflow/divide-by-zero 告警，掩盖真实问题。
        # einsum(optimize=True) 在这个形状下还更快一点。
        return np.einsum("ij,kj->ik", pts - self.center, self.rotation, optimize=True)


def project(camera: Camera, pose: Pose, pts_world: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """世界点 → 像素。返回 (u, v, valid)，valid 标记在相机前方的点。"""
    pc = pose.world_to_camera(pts_world)
    z = pc[:, 2]
    valid = z > 1e-6
    safe_z = np.where(valid, z, 1.0)
    xn = pc[:, 0] / safe_z
    yn = pc[:, 1] / safe_z
    # 像幅外的射线即使被畸变折回像素坐标，也不是这张相片上的观测
    limit = camera.format_normalized_radius()
    valid = valid & ((xn * xn + yn * yn) <= limit * limit)
    u, v = camera.project_normalized(xn, yn)
    return u, v, valid


def ray_directions(camera: Camera, pose: Pose, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """像素 → 世界系的视线方向（未归一化，z 分量按相机系 1 缩放）。"""
    xn, yn = camera.undistort_pixels(u, v)
    dirs_cam = np.stack([xn, yn, np.ones_like(xn)], axis=-1)
    return np.einsum("ij,jk->ik", dirs_cam, pose.rotation, optimize=True)


def intersect_plane(
    camera: Camera, pose: Pose, u: np.ndarray, v: np.ndarray, height: float
) -> np.ndarray:
    """视线与水平面 Z=height 求交，用于足迹估算与扫描初值。"""
    dirs = ray_directions(camera, pose, u, v)
    dz = dirs[:, 2]
    dz = np.where(np.abs(dz) < 1e-9, -1e-9, dz)
    t = (height - pose.center[2]) / dz
    return pose.center + dirs * t[:, None]


def footprint(camera: Camera, pose: Pose, height: float) -> np.ndarray:
    """影像四角在 Z=height 平面上的地面足迹，顺序为左上/右上/右下/左下。"""
    w, h = camera.width - 1.0, camera.height - 1.0
    u = np.array([0.0, w, w, 0.0])
    v = np.array([0.0, 0.0, h, h])
    return intersect_plane(camera, pose, u, v, height)[:, :2]
