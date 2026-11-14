"""显卡 SIFT。算法是 Mårten Björkman 的 CudaSift，描述子再做成 RootSIFT。

和 CPU 上的分块 OpenCV SIFT 不是同一组点。任务把 GPU 设成自动或指定卡号、
并且这台机器编出了 libsiftgpu.so 时，特征提取走这里。库不在就抛错，由调用方退回 CPU。
"""

from __future__ import annotations

import ctypes
import threading
from pathlib import Path

import numpy as np

# 2048×1536 的 S810 样本上，这个 DoG 阈值能把点数打到 8192 上限附近。
CONTRAST_THRESHOLD = 1.5
_LIB_PATH = Path(__file__).resolve().parent / "native" / "cudasift" / "libsiftgpu.so"

_lock = threading.Lock()
_lib = None
_loaded = False


class GpuSiftUnavailable(RuntimeError):
    """显卡 SIFT 库缺失或初始化失败。"""


def _load():
    global _lib, _loaded
    if _loaded:
        return _lib
    _loaded = True
    if not _LIB_PATH.is_file():
        return None
    lib = ctypes.CDLL(str(_LIB_PATH))
    lib.sift_init.argtypes = [ctypes.c_int]
    lib.sift_init.restype = ctypes.c_int
    lib.sift_extract.argtypes = [
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_float,
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.sift_extract.restype = ctypes.c_int
    _lib = lib
    return lib


def prepare(device: int) -> None:
    """加载库并选中这张卡。库不在或初始化失败时抛 GpuSiftUnavailable。"""
    lib = _load()
    if lib is None:
        raise GpuSiftUnavailable(f"未找到 {_LIB_PATH.name}")
    with _lock:
        rc = int(lib.sift_init(int(device)))
    if rc != 0:
        raise GpuSiftUnavailable(f"显卡 {device} 初始化失败（{rc}）")


def extract(gray: np.ndarray, *, device: int, max_features: int) -> tuple[np.ndarray, np.ndarray]:
    """整图 CudaSift，最多 max_features 个点。返回 (关键点 N×4, SIFT 描述子)，调用方再做 RootSIFT。"""
    lib = _load()
    if lib is None:
        raise GpuSiftUnavailable(f"未找到 {_LIB_PATH.name}")
    limit = int(max_features)
    host = np.ascontiguousarray(gray, dtype=np.float32)
    if host.ndim != 2:
        raise ValueError("显卡 SIFT 只接受单通道灰度")
    height, width = host.shape
    keypoints = np.empty((limit, 4), np.float32)
    descriptors = np.empty((limit, 128), np.float32)
    count = ctypes.c_int()
    with _lock:
        rc = int(
            lib.sift_extract(
                host.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                int(width),
                int(height),
                limit,
                ctypes.c_float(CONTRAST_THRESHOLD),
                keypoints.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                descriptors.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                ctypes.byref(count),
            )
        )
    if rc != 0:
        raise GpuSiftUnavailable(f"显卡提点失败（{rc}）")
    n = max(0, min(int(count.value), limit))
    return keypoints[:n].copy(), descriptors[:n].copy()
