"""特征提取：分块 RootSIFT。

RootSIFT 来自 Arandjelović & Zisserman, CVPR 2012：SIFT 描述子做 L1 归一化再开方，
欧氏距离即等价于原描述子的 Hellinger 距离，检索精度明显提升且零额外开销，
COLMAP / OpenSfM 均默认采用。

分块提取保证特征在像幅内空间均匀。纹理富集区（如林地）若不分块会吃满整张图的配额，
导致弱纹理区（道路、裸土）无点可匹配，条带数据尤其明显。
LiMapper 报告里「影像关键点数最大值 8192」说明商业软件同样做了总量截断。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zipfile

import numpy as np

from ms_mosaic.rawio import read_gray8

MAX_FEATURES = 8192
TILE_GRID = (4, 3)  # 列 x 行
CONTRAST_THRESHOLD = 0.0055
EDGE_THRESHOLD = 12.0


@dataclass
class Features:
    """一张影像的特征。keypoints 为 (N, 4) 的 u, v, size, angle。"""

    keypoints: np.ndarray
    descriptors: np.ndarray

    def __len__(self) -> int:
        return int(self.keypoints.shape[0])


def root_sift(descriptors: np.ndarray, eps: float = 1e-7) -> np.ndarray:
    """SIFT → RootSIFT。"""
    desc = np.asarray(descriptors, np.float32)
    if desc.size == 0:
        return desc.reshape(0, 128)
    desc /= desc.sum(axis=1, keepdims=True) + eps
    return np.sqrt(desc, out=desc)


def _sift(n_features: int):
    import cv2

    return cv2.SIFT_create(
        nfeatures=int(n_features),
        contrastThreshold=CONTRAST_THRESHOLD,
        edgeThreshold=EDGE_THRESHOLD,
    )


def extract(
    gray: np.ndarray,
    *,
    max_features: int = MAX_FEATURES,
    tile_grid: tuple[int, int] = TILE_GRID,
) -> Features:
    """分块提 RootSIFT。返回按响应强度排序后截断到 max_features 的结果。"""
    height, width = gray.shape[:2]
    cols, rows = tile_grid
    per_tile = max(1, int(round(max_features / (cols * rows) * 1.5)))
    sift = _sift(per_tile)

    all_kp: list[np.ndarray] = []
    all_desc: list[np.ndarray] = []
    all_resp: list[np.ndarray] = []
    # 分块边界留重叠，避免跨块的特征在块边缘被截断
    margin = 24
    for r in range(rows):
        y0 = int(round(r * height / rows))
        y1 = int(round((r + 1) * height / rows))
        for c in range(cols):
            x0 = int(round(c * width / cols))
            x1 = int(round((c + 1) * width / cols))
            sy0, sy1 = max(0, y0 - margin), min(height, y1 + margin)
            sx0, sx1 = max(0, x0 - margin), min(width, x1 + margin)
            patch = gray[sy0:sy1, sx0:sx1]
            kps, desc = sift.detectAndCompute(patch, None)
            if not kps:
                continue
            uv = np.array([[k.pt[0] + sx0, k.pt[1] + sy0, k.size, k.angle] for k in kps])
            resp = np.array([k.response for k in kps])
            # 只保留落在本块正式范围内的点，重叠区交给相邻块，避免重复
            inside = (uv[:, 0] >= x0) & (uv[:, 0] < x1) & (uv[:, 1] >= y0) & (uv[:, 1] < y1)
            if not inside.any():
                continue
            all_kp.append(uv[inside])
            all_desc.append(desc[inside])
            all_resp.append(resp[inside])

    if not all_kp:
        return Features(np.zeros((0, 4)), np.zeros((0, 128), np.float32))

    kp = np.concatenate(all_kp, axis=0)
    desc = np.concatenate(all_desc, axis=0)
    resp = np.concatenate(all_resp, axis=0)
    if kp.shape[0] > max_features:
        keep = np.argsort(-resp)[:max_features]
        keep.sort()
        kp, desc = kp[keep], desc[keep]
    return Features(kp, root_sift(desc))


def _cache_path(path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{path.stem}.npz"


def extract_file(
    path: Path, *, cache_dir: Path | None = None, max_features: int = MAX_FEATURES
) -> Features:
    """带缓存的单文件提取，缓存用于断点续跑（REQ-07-02）。

    任务被杀死时可能留下半截 npz（numpy 用 zip 容器）。读失败就丢掉重提，
    写入先落到 .tmp 再替换，避免下次再读到半截文件。
    """
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _cache_path(path, cache_dir)
        if cache_path.exists():
            try:
                with np.load(cache_path) as data:
                    return Features(np.asarray(data["keypoints"]), np.asarray(data["descriptors"]))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                cache_path.unlink(missing_ok=True)
    feats = extract(read_gray8(path), max_features=max_features)
    if cache_path is not None:
        tmp = cache_path.with_name(cache_path.stem + ".tmp.npz")
        np.savez_compressed(
            tmp, keypoints=feats.keypoints, descriptors=feats.descriptors.astype(np.float32)
        )
        tmp.replace(cache_path)
    return feats


def load_keypoints(path: Path, *, cache_dir: Path) -> np.ndarray:
    """只读缓存里的关键点坐标，不加载描述子。

    交会只用 u/v。1992 张 × 8192 × 128 float32 描述子约 8 GB，
    全量驻留会在 24 GB 引擎里把前方交会直接 oom_kill。
    """
    cache_path = _cache_path(path, cache_dir)
    if not cache_path.is_file():
        return extract_file(path, cache_dir=cache_dir).keypoints
    try:
        with np.load(cache_path) as data:
            return np.asarray(data["keypoints"])
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        cache_path.unlink(missing_ok=True)
        return extract_file(path, cache_dir=cache_dir).keypoints
