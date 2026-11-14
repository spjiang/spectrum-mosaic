"""GPU / numpy 精确 kNN 匹配。

CPU 主路径仍用 FLANN 近似搜索（Muja & Lowe 2009）。GPU 路径按 COLMAP exhaustive
matcher：算完整距离矩阵取 k=2，再做 Lowe 比值与双向交叉校验。精确搜索不比近似差，
但不会和 FLANN 逐对相同。device=None 时走 numpy，方程与 CuPy 相同，便于无卡对拍。

出处：Lowe, IJCV 2004；Schönberger & Frahm, CVPR 2016（COLMAP exhaustive）；
Johnson et al., *Billion-scale similarity search with GPUs*, 2017。
"""

from __future__ import annotations

import numpy as np

from ms_mosaic.matching import RATIO_TEST
from ms_mosaic.xp import array_module, asnumpy, on_device

# 距离矩阵一块不超过约 256 MB，特征更多时按查询行分块，结果与整块相同。
_DIST_BYTES_CAP = 256 * 1024 * 1024
_GEMM_OK: bool | None = None
_NVIDIA_PRELOADED = False
_L2SQ_KERNEL = None
_L2SQ_SRC = r"""
extern "C" __global__
void l2sq(const float* a, const float* b, float* d2, int na, int nb, int dim) {
  int i = blockIdx.y * blockDim.y + threadIdx.y;
  int j = blockIdx.x * blockDim.x + threadIdx.x;
  if (i >= na || j >= nb) return;
  const float* ai = a + i * dim;
  const float* bj = b + j * dim;
  float s = 0.f;
  for (int k = 0; k < dim; ++k) {
    float v = ai[k] - bj[k];
    s += v * v;
  }
  d2[i * nb + j] = s;
}
"""


def _l2sq_kernel(xp):
    global _L2SQ_KERNEL
    if _L2SQ_KERNEL is None:
        _L2SQ_KERNEL = xp.RawKernel(_L2SQ_SRC, "l2sq")
    return _L2SQ_KERNEL


def _preload_nvidia_libs() -> None:
    """把 pip 装的 CUDA 12 运行库提前载入。缺 libcublas.so.12 时 GEMM 会 ImportError。"""
    global _NVIDIA_PRELOADED
    if _NVIDIA_PRELOADED:
        return
    _NVIDIA_PRELOADED = True
    import ctypes
    import glob
    from pathlib import Path
    import sys

    roots = glob.glob(str(Path(sys.prefix) / "lib" / "python*" / "site-packages" / "nvidia" / "*" / "lib"))
    names = ("libcudart.so.12", "libnvJitLink.so.12", "libcublasLt.so.12", "libcublas.so.12")
    for name in names:
        for root in roots:
            matches = glob.glob(str(Path(root) / name)) + glob.glob(str(Path(root) / (name + "*")))
            if not matches:
                continue
            try:
                ctypes.CDLL(sorted(matches)[0], mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue
            break


def _l2_squared(a, b, aa, bb, xp, device: int | None):
    """||a-b||^2。优先 GEMM（cuBLAS）；198 上若缺 libcublas.so.12 则走 RawKernel。"""
    global _GEMM_OK
    if device is not None and _GEMM_OK is not False:
        _preload_nvidia_libs()
    if device is None or _GEMM_OK is not False:
        try:
            out = aa[:, None] + bb[None, :] - (2.0 * (a @ b.T))
            _GEMM_OK = True if device is not None else _GEMM_OK
            return out
        except ImportError:
            if device is None:
                raise
            _GEMM_OK = False
    kernel = _l2sq_kernel(xp)
    n_a, dim = int(a.shape[0]), int(a.shape[1])
    n_b = int(b.shape[0])
    d2 = xp.empty((n_a, n_b), dtype=xp.float32)
    block = (16, 16)
    grid = ((n_b + 15) // 16, (n_a + 15) // 16)
    kernel(grid, block, (a, b, d2, np.int32(n_a), np.int32(n_b), np.int32(dim)))
    return d2


def _knn_chunk_rows(n_a: int, n_b: int, itemsize: int = 4) -> int:
    row_bytes = max(1, int(n_b) * int(itemsize))
    return max(1, min(int(n_a), _DIST_BYTES_CAP // row_bytes))


def _is_oom(exc: BaseException) -> bool:
    from ms_mosaic.ortho_gpu import is_out_of_memory

    return is_out_of_memory(exc)


def knn_ratio_match(
    desc_a: np.ndarray,
    desc_b: np.ndarray,
    *,
    ratio: float = RATIO_TEST,
    device: int | None = None,
) -> np.ndarray:
    """单向精确 kNN + Lowe ratio test，返回 (N, 2) 下标。"""
    desc_a = np.ascontiguousarray(desc_a, np.float32)
    desc_b = np.ascontiguousarray(desc_b, np.float32)
    if desc_a.shape[0] < 2 or desc_b.shape[0] < 2:
        return np.zeros((0, 2), int)
    try:
        return _knn_ratio_match(desc_a, desc_b, ratio=ratio, device=device)
    except Exception as exc:  # noqa: BLE001
        if device is not None and _is_oom(exc):
            return _knn_ratio_match(desc_a, desc_b, ratio=ratio, device=None)
        raise


def _knn_ratio_match(
    desc_a: np.ndarray,
    desc_b: np.ndarray,
    *,
    ratio: float,
    device: int | None,
) -> np.ndarray:
    xp = array_module(device)
    n_a = int(desc_a.shape[0])
    parts: list[np.ndarray] = []
    with on_device(device):
        b = xp.asarray(desc_b, dtype=xp.float32)
        bb = (b * b).sum(axis=1)
        rows = _knn_chunk_rows(n_a, int(desc_b.shape[0]))
        for start in range(0, n_a, rows):
            a = xp.asarray(desc_a[start : start + rows], dtype=xp.float32)
            aa = (a * a).sum(axis=1)
            # ||a-b||^2 = |a|^2 + |b|^2 - 2 a·b。比值用欧氏距离，不是平方。
            d2 = _l2_squared(a, b, aa, bb, xp, device)
            pair = xp.argpartition(d2, kth=1, axis=1)[:, :2]
            dd = xp.take_along_axis(d2, pair, axis=1)
            order = xp.argsort(dd, axis=1)
            nn = xp.take_along_axis(pair, order, axis=1)
            dsort = xp.take_along_axis(dd, order, axis=1)
            d1 = xp.sqrt(xp.maximum(dsort[:, 0], 0))
            d2e = xp.sqrt(xp.maximum(dsort[:, 1], 0))
            keep = d1 < (ratio * d2e)
            qi = xp.where(keep)[0]
            if int(qi.size) == 0:
                continue
            tj = nn[keep, 0]
            chunk = xp.stack([qi + start, tj], axis=1)
            parts.append(np.asarray(asnumpy(chunk), dtype=int))
    if not parts:
        return np.zeros((0, 2), int)
    return np.concatenate(parts, axis=0)


def cross_check(
    desc_a: np.ndarray,
    desc_b: np.ndarray,
    *,
    ratio: float = RATIO_TEST,
    device: int | None = None,
) -> np.ndarray:
    """双向匹配取交集，消除一对多。规则与 CPU matching.cross_check 相同。"""
    ab = knn_ratio_match(desc_a, desc_b, ratio=ratio, device=device)
    ba = knn_ratio_match(desc_b, desc_a, ratio=ratio, device=device)
    if ab.size == 0 or ba.size == 0:
        return np.zeros((0, 2), int)
    back = {int(query_b): int(train_a) for query_b, train_a in ba}
    keep = [(int(a), int(b)) for a, b in ab if back.get(int(b), -1) == int(a)]
    return np.array(keep, int).reshape(-1, 2)
