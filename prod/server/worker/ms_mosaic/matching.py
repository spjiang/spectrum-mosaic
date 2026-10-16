"""特征匹配与几何校验。

流程沿用业界通行做法（COLMAP / OpenSfM）：
1. FLANN kd-tree 最近邻 k=2；
2. Lowe ratio test（RootSIFT 下阈值可放到 0.8）；
3. 双向互为最近邻的交叉校验，去掉一对多；
4. 本质矩阵 RANSAC（Nistér 五点法）剔粗差 —— 内参有近似值，用本质矩阵比基础矩阵
   约束更强；
5. 平面退化检测：同时拟合单应，若单应内点数接近本质矩阵内点数，说明该像对近似
   共面（本批数据大量农田、裸土属于这种），改用单应内点，避免本质矩阵在退化构型下
   给出错误的相对位姿。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ms_mosaic.camera import Camera
from ms_mosaic.features import Features

RATIO_TEST = 0.80
RANSAC_THRESHOLD_PX = 2.0
MIN_INLIERS = 20
PLANAR_RATIO = 0.90


@dataclass(frozen=True)
class PairMatches:
    i: int
    j: int
    indices: np.ndarray  # (N, 2) 分别是两张图的特征下标
    model: str  # "essential" 或 "homography"

    def __len__(self) -> int:
        return int(self.indices.shape[0])


def _flann():
    import cv2

    # trees/checks 取 COLMAP 量级；checks 偏小会在弱纹理区漏掉真近邻
    return cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 128})


def knn_ratio_match(
    desc_a: np.ndarray, desc_b: np.ndarray, *, ratio: float = RATIO_TEST
) -> np.ndarray:
    """单向 kNN + ratio test，返回 (N, 2) 下标。"""
    if desc_a.shape[0] < 2 or desc_b.shape[0] < 2:
        return np.zeros((0, 2), int)
    pairs = _flann().knnMatch(
        np.ascontiguousarray(desc_a, np.float32), np.ascontiguousarray(desc_b, np.float32), k=2
    )
    out = [
        (m.queryIdx, m.trainIdx)
        for m, n in (p for p in pairs if len(p) == 2)
        if m.distance < ratio * n.distance
    ]
    return np.array(out, int).reshape(-1, 2)


def cross_check(
    desc_a: np.ndarray, desc_b: np.ndarray, *, ratio: float = RATIO_TEST
) -> np.ndarray:
    """双向匹配取交集，消除一对多。"""
    ab = knn_ratio_match(desc_a, desc_b, ratio=ratio)
    ba = knn_ratio_match(desc_b, desc_a, ratio=ratio)
    if ab.size == 0 or ba.size == 0:
        return np.zeros((0, 2), int)
    # ba 的列是 (b 的下标, a 的下标)，建 b → a 的反查表
    back = {int(query_b): int(train_a) for query_b, train_a in ba}
    keep = [(int(a), int(b)) for a, b in ab if back.get(int(b), -1) == int(a)]
    return np.array(keep, int).reshape(-1, 2)


def geometric_verify(
    pts_a: np.ndarray,
    pts_b: np.ndarray,
    cam_a: Camera,
    cam_b: Camera,
    *,
    threshold_px: float = RANSAC_THRESHOLD_PX,
    planar_ratio: float = PLANAR_RATIO,
) -> tuple[np.ndarray, str]:
    """返回 (内点布尔掩码, 采用的模型)。"""
    import cv2

    if pts_a.shape[0] < 8:
        return np.zeros(pts_a.shape[0], bool), "none"

    xa, ya = cam_a.undistort_pixels(pts_a[:, 0], pts_a[:, 1])
    xb, yb = cam_b.undistort_pixels(pts_b[:, 0], pts_b[:, 1])
    norm_a = np.stack([xa, ya], axis=1)
    norm_b = np.stack([xb, yb], axis=1)
    # 归一化平面上的阈值：像素阈值除以两台相机焦距的均值
    thr = threshold_px / (0.5 * (cam_a.f + cam_b.f))

    essential, mask_e = cv2.findEssentialMat(
        norm_a, norm_b, np.eye(3), method=cv2.USAC_MAGSAC, prob=0.9999, threshold=thr
    )
    inl_e = (
        np.zeros(pts_a.shape[0], bool)
        if essential is None or mask_e is None
        else mask_e.ravel().astype(bool)
    )

    _, mask_h = cv2.findHomography(
        norm_a, norm_b, method=cv2.USAC_MAGSAC, ransacReprojThreshold=thr, confidence=0.9999
    )
    inl_h = np.zeros(pts_a.shape[0], bool) if mask_h is None else mask_h.ravel().astype(bool)

    n_e, n_h = int(inl_e.sum()), int(inl_h.sum())
    if n_h >= planar_ratio * max(n_e, 1) and n_h >= n_e:
        return inl_h, "homography"
    return inl_e, "essential"


def match_pair(
    i: int,
    j: int,
    feats_a: Features,
    feats_b: Features,
    cam_a: Camera,
    cam_b: Camera,
    *,
    ratio: float = RATIO_TEST,
    threshold_px: float = RANSAC_THRESHOLD_PX,
    min_inliers: int = MIN_INLIERS,
) -> PairMatches | None:
    idx = cross_check(feats_a.descriptors, feats_b.descriptors, ratio=ratio)
    if idx.shape[0] < min_inliers:
        return None
    pts_a = feats_a.keypoints[idx[:, 0], :2]
    pts_b = feats_b.keypoints[idx[:, 1], :2]
    inliers, model = geometric_verify(pts_a, pts_b, cam_a, cam_b, threshold_px=threshold_px)
    if int(inliers.sum()) < min_inliers:
        return None
    return PairMatches(i, j, idx[inliers], model)
