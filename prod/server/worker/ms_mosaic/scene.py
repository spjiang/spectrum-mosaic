"""测区（block）组装：把扫描到的曝光变成相机 + 外方位初值 + GPS 观测。

一次曝光有 8 台相机（Color 与 7 个波段），同一波段共用一套内参，这与 LiMapper
报告里「组 0 … 组 7」的分组完全一致，自标定也按组进行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ms_mosaic.camera import Camera, Pose, wrap_yaw_deg
from ms_mosaic.catalog import Shot
from ms_mosaic.geo import epsg_utm, lonlat_to_utm
from ms_mosaic.rawio import image_size

PRIMARY_BAND = "Color"  # 与 LiMapper「主波段 = 组 0」一致：RGB 纹理最丰富
BAND_ORDER = ("Color", "450nm", "550nm", "650nm", "720nm", "750nm", "800nm", "850nm")
MAX_TILT_DEG = 60.0  # REQ-03-03 / 报告「最大倾斜角 60 度」
MIN_AGL_M = 5.0

# GPS/IMU 先验的标准差。XMP 给出 GPSXYAccuracy=3 m、GPSZAccuracy=5 m；
# 姿态无精度字段，按消费级云台给经验值，并在平差里用弱权。
SIGMA_XY_M = 3.0
SIGMA_Z_M = 5.0
SIGMA_ATTITUDE_DEG = 3.0


@dataclass(frozen=True)
class ImageRef:
    index: int
    shot_index: int
    band: str
    path: Path
    group: int

    @property
    def is_primary(self) -> bool:
        return self.band == PRIMARY_BAND


@dataclass
class Block:
    crs: str
    images: list[ImageRef] = field(default_factory=list)
    cameras: dict[str, Camera] = field(default_factory=dict)
    poses: dict[int, Pose] = field(default_factory=dict)
    gps: dict[int, np.ndarray] = field(default_factory=dict)
    attitude: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    ground_z: float = 0.0

    def by_band(self, band: str) -> list[ImageRef]:
        return [im for im in self.images if im.band == band]

    def primary(self) -> list[ImageRef]:
        return self.by_band(PRIMARY_BAND)

    def camera_of(self, image: ImageRef) -> Camera:
        return self.cameras[image.band]

    def subset(self, indices) -> tuple[dict[str, Camera], dict[int, Pose]]:
        wanted = set(int(i) for i in indices)
        by_index = {im.index: im for im in self.images}
        cams = {i: self.cameras[by_index[i].band] for i in wanted if i in by_index}
        poses = {i: self.poses[i] for i in wanted if i in self.poses}
        return cams, poses


def tilt_deg(pitch_deg: float, roll_deg: float) -> float:
    """光轴偏离天底的角度。pitch=-90、roll=0 时为 0。"""
    from ms_mosaic.camera import rotation_from_ypr

    direction = rotation_from_ypr(0.0, pitch_deg, roll_deg)[2, :]
    return float(np.degrees(np.arccos(min(1.0, max(-1.0, -direction[2])))))


def usable_shots(
    shots: Mapping[int, Shot],
    *,
    min_agl_m: float = MIN_AGL_M,
    max_tilt_deg: float = MAX_TILT_DEG,
    drop_white_panel: bool = True,
    require_pos: bool = True,
) -> tuple[dict[int, Shot], dict[str, int]]:
    """过滤白板、地面帧、无 POS、大倾角。返回 (保留, 各原因计数)。"""
    keep: dict[int, Shot] = {}
    reasons = {"no_pos": 0, "white_panel": 0, "low_agl": 0, "high_tilt": 0}
    for idx, shot in shots.items():
        if drop_white_panel and shot.role == "W":
            reasons["white_panel"] += 1
            continue
        if shot.pos is None:
            # 无 POS 不能给 GNSS/INS 初值；require_pos=False 也必须丢掉。
            reasons["no_pos"] += 1
            continue
        if not require_pos:
            pass
        if shot.pos.agl_m < min_agl_m:
            reasons["low_agl"] += 1
            continue
        if tilt_deg(shot.pos.pitch_deg, shot.pos.roll_deg) > max_tilt_deg:
            reasons["high_tilt"] += 1
            continue
        keep[idx] = shot
    return keep, reasons


def build_block(shots: Mapping[int, Shot], *, crs: str | None = None) -> Block:
    """由曝光构造测区。相机中心取 XMP 融合高程，缺则用 EXIF GPS，与 DSM 同一垂直基准。"""
    if not shots:
        raise ValueError("没有可用曝光")
    first = shots[min(shots)]
    assert first.pos is not None
    crs = crs or epsg_utm(first.pos.lon, first.pos.lat)

    block = Block(crs=crs)
    sizes: dict[str, tuple[int, int]] = {}
    agls: list[float] = []
    next_index = 0

    for shot_index in sorted(shots):
        shot = shots[shot_index]
        pos = shot.pos
        assert pos is not None
        east, north = lonlat_to_utm(pos.lon, pos.lat, crs)
        center = np.array([east, north, pos.alt_m], float)
        yaw = wrap_yaw_deg(pos.yaw_deg)
        agls.append(pos.agl_m)

        records = []
        if shot.rgb is not None:
            records.append(("Color", shot.rgb.path))
        for band in BAND_ORDER[1:]:
            rec = shot.ms.get(band)
            if rec is not None:
                records.append((band, rec.path))

        for band, path in records:
            if band not in sizes:
                sizes[band] = image_size(path)
                width, height = sizes[band]
                block.cameras[band] = Camera.initial(
                    band, width, height, kind="rgb" if band == "Color" else "ms"
                )
            image = ImageRef(
                index=next_index,
                shot_index=shot_index,
                band=band,
                path=path,
                group=BAND_ORDER.index(band),
            )
            block.images.append(image)
            block.poses[next_index] = Pose.from_ypr(center, yaw, pos.pitch_deg, pos.roll_deg)
            block.gps[next_index] = center.copy()
            block.attitude[next_index] = (yaw, pos.pitch_deg, pos.roll_deg)
            next_index += 1

    mean_alt = float(np.mean([block.gps[i][2] for i in block.gps]))
    block.ground_z = mean_alt - float(np.mean(agls))
    return block


def transfer_band(
    block: Block,
    cameras: dict[str, Camera],
    poses: dict[int, Pose],
    band: str,
) -> tuple[dict[int, Camera], dict[int, Pose], dict[int, Path]]:
    """把主波段空三得到的外方位搬到同一曝光的其他波段。

    MAX-S810 八个镜头刚体固连，相对方位未知时最稳的一阶近似是：同一曝光共用
    平台位姿，内参按主波段自标定相对初值的比例缩放焦距，并把主点偏移与畸变
    一并迁移。完整的 RigRelatives 联合平差是下一步，但这一步已经能先把 8 张
    正射出出来。
    """
    primary_cam = cameras[PRIMARY_BAND]
    primary_init = Camera.initial(
        PRIMARY_BAND, primary_cam.width, primary_cam.height, kind="rgb"
    )
    if band == PRIMARY_BAND:
        cam = primary_cam
    else:
        init = block.cameras[band]
        ratio = primary_cam.f / primary_init.f
        vec = init.to_vector()
        vec[0] *= ratio
        vec[1] = init.cx + (primary_cam.cx - primary_init.cx)
        vec[2] = init.cy + (primary_cam.cy - primary_init.cy)
        vec[3:] = primary_cam.to_vector()[3:]
        cam = init.with_vector(vec)

    primary_by_shot = {im.shot_index: im.index for im in block.primary()}
    out_cam: dict[int, Camera] = {}
    out_pose: dict[int, Pose] = {}
    out_path: dict[int, Path] = {}
    for im in block.by_band(band):
        src = primary_by_shot.get(im.shot_index)
        if src is None or src not in poses:
            continue
        out_cam[im.index] = cam
        out_pose[im.index] = poses[src]
        out_path[im.index] = Path(im.path)
    return out_cam, out_pose, out_path
