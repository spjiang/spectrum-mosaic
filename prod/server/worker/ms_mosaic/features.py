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
import threading
import time
import zipfile

import numpy as np

from ms_mosaic.rawio import read_gray8

_extract_lock = threading.Lock()
_extract_time = {"read": 0.0, "gpu": 0.0}


def reset_extract_time() -> None:
    with _extract_lock:
        _extract_time["read"] = 0.0
        _extract_time["gpu"] = 0.0


def extract_time_totals() -> tuple[float, float]:
    with _extract_lock:
        return _extract_time["read"], _extract_time["gpu"]


def _add_extract_time(read_s: float, gpu_s: float) -> None:
    with _extract_lock:
        _extract_time["read"] += read_s
        _extract_time["gpu"] += gpu_s

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


def _cache_matches_limit(data, max_features: int, extractor: int) -> bool:
    """缓存是否按当前上限、当前提取器提取。

    新缓存写了 max_features，必须与本次相同。旧缓存没有这个字段，当时按 8192 截断：
    点数少于 8192 说明没有被截断，本次上限不低于已有点数就可以用；
    点数正好 8192 说明可能被截断过，只有本次仍是 8192 才沿用。
    extractor：0 是 CPU 分块 RootSIFT，1 是显卡整图 SIFT。旧缓存没写这个字段，视为 CPU。
    """
    n = int(np.asarray(data["keypoints"]).shape[0])
    limit = int(max_features)
    stored_extractor = 0
    if "extractor" in getattr(data, "files", ()):
        stored_extractor = int(np.asarray(data["extractor"]).reshape(-1)[0])
    if stored_extractor != int(extractor):
        return False
    if "max_features" in data.files:
        stored = int(np.asarray(data["max_features"]).reshape(-1)[0])
        return stored == limit
    if n < MAX_FEATURES:
        return limit >= n
    return limit == MAX_FEATURES


def extract_file(
    path: Path,
    *,
    cache_dir: Path | None = None,
    max_features: int = MAX_FEATURES,
    device: int | None = None,
) -> Features:
    """带缓存的单文件提取，缓存用于断点续跑（REQ-07-02）。

    任务被杀死时可能留下半截 npz（numpy 用 zip 容器）。读失败就丢掉重提，
    写入先落到 .tmp 再替换，避免下次再读到半截文件。
    上限或提取器与缓存不一致时重新提取。device 不为空时走显卡整图 SIFT。
    """
    limit = int(max_features)
    if limit < 1:
        raise ValueError(f"每张影像特征点上限必须是正整数，当前 {max_features}")
    extractor = 0 if device is None else 1
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _cache_path(path, cache_dir)
        if cache_path.exists():
            try:
                with np.load(cache_path) as data:
                    if _cache_matches_limit(data, limit, extractor):
                        return Features(np.asarray(data["keypoints"]), np.asarray(data["descriptors"]))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                cache_path.unlink(missing_ok=True)
    if device is None:
        feats = extract(read_gray8(path), max_features=limit)
    else:
        from ms_mosaic.sift_gpu import extract as extract_gpu

        started = time.perf_counter()
        gray = read_gray8(path)
        _add_extract_time(time.perf_counter() - started, 0.0)
        started = time.perf_counter()
        keypoints, descriptors = extract_gpu(gray, device=int(device), max_features=limit)
        _add_extract_time(0.0, time.perf_counter() - started)
        # CudaSift 描述子已做 L2 归一，个别分箱有 1e-7 的负零头，开方前夹到 0。
        feats = Features(keypoints, root_sift(np.maximum(descriptors, 0)))
    if cache_path is not None:
        tmp = cache_path.with_name(cache_path.stem + ".tmp.npz")
        np.savez_compressed(
            tmp,
            keypoints=feats.keypoints,
            descriptors=feats.descriptors.astype(np.float32),
            max_features=np.int32(limit),
            extractor=np.int32(extractor),
        )
        tmp.replace(cache_path)
    return feats


def load_keypoints(path: Path, *, cache_dir: Path, max_features: int = MAX_FEATURES) -> np.ndarray:
    """只读缓存里的关键点坐标，不加载描述子。

    交会只用 u/v。1992 张 × 8192 × 128 float32 描述子约 8 GB，
    全量驻留会在 24 GB 引擎里把前方交会直接 oom_kill。
    缓存缺失或损坏时按 max_features 重提，与提取阶段用同一个上限。
    """
    limit = int(max_features)
    cache_path = _cache_path(path, cache_dir)
    if not cache_path.is_file():
        return extract_file(path, cache_dir=cache_dir, max_features=limit).keypoints
    try:
        with np.load(cache_path) as data:
            return np.asarray(data["keypoints"])
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        cache_path.unlink(missing_ok=True)
        return extract_file(path, cache_dir=cache_dir, max_features=limit).keypoints
