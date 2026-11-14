"""精确 kNN 与 numpy 参考对拍。不和 FLANN 比逐对，近似搜索本来就会漏近邻。"""

import numpy as np
import pytest

from ms_mosaic import matching_gpu


def _unit(n: int, dim: int = 128, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    d = rng.standard_normal((n, dim)).astype(np.float32)
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    return d


def _brute_knn(desc_a: np.ndarray, desc_b: np.ndarray, ratio: float) -> np.ndarray:
    diff = desc_a[:, None, :] - desc_b[None, :, :]
    dist = np.sqrt((diff * diff).sum(axis=2))
    nn = np.argpartition(dist, 1, axis=1)[:, :2]
    dd = np.take_along_axis(dist, nn, axis=1)
    order = np.argsort(dd, axis=1)
    nn = np.take_along_axis(nn, order, axis=1)
    dd = np.take_along_axis(dd, order, axis=1)
    keep = dd[:, 0] < ratio * dd[:, 1]
    qi = np.where(keep)[0]
    return np.stack([qi, nn[keep, 0]], axis=1).astype(int)


def test_knn_numpy_matches_brute_force():
    a, b = _unit(40, seed=1), _unit(55, seed=2)
    got = matching_gpu.knn_ratio_match(a, b, device=None)
    ref = _brute_knn(a, b, matching_gpu.RATIO_TEST)
    assert set(map(tuple, got.tolist())) == set(map(tuple, ref.tolist()))


def test_knn_chunked_matches_full_matrix(monkeypatch):
    a, b = _unit(30, seed=3), _unit(28, seed=4)
    full = matching_gpu.knn_ratio_match(a, b, device=None)
    monkeypatch.setattr(matching_gpu, "_DIST_BYTES_CAP", 28 * 4 * 7)
    chunked = matching_gpu.knn_ratio_match(a, b, device=None)
    assert set(map(tuple, chunked)) == set(map(tuple, full))


def test_cross_check_is_mutual_nearest():
    a = _unit(24, seed=5)
    b = a.copy()
    b += 0.01 * _unit(24, seed=6)
    b /= np.linalg.norm(b, axis=1, keepdims=True)
    idx = matching_gpu.cross_check(a, b, device=None)
    assert idx.shape[0] > 10
    back = matching_gpu.knn_ratio_match(b, a, device=None)
    back_map = {int(i): int(j) for i, j in back}
    for i, j in idx:
        assert back_map.get(int(j), -1) == int(i)


def test_cross_check_rejects_repetitive_texture():
    rng = np.random.default_rng(3)
    base = rng.random(128).astype(np.float32)
    desc = np.tile(base, (60, 1)) + rng.normal(0, 0.002, (60, 128)).astype(np.float32)
    desc /= np.linalg.norm(desc, axis=1, keepdims=True)
    other = np.tile(base, (60, 1)) + rng.normal(0, 0.002, (60, 128)).astype(np.float32)
    other /= np.linalg.norm(other, axis=1, keepdims=True)
    assert matching_gpu.cross_check(desc, other, device=None).shape[0] == 0


def test_empty_descriptors():
    empty = np.zeros((0, 128), np.float32)
    one = _unit(3, seed=7)
    assert matching_gpu.knn_ratio_match(empty, one, device=None).shape == (0, 2)
    assert matching_gpu.cross_check(one[:1], one, device=None).shape == (0, 2)


def test_numpy_path_does_not_import_cupy(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "cupy", None)
    a, b = _unit(12, seed=8), _unit(12, seed=9)
    got = matching_gpu.cross_check(a, b, device=None)
    assert got.ndim == 2 and got.shape[1] == 2


@pytest.mark.gpu
def test_cupy_matches_numpy_when_available():
    cupy = pytest.importorskip("cupy")
    if cupy.cuda.runtime.getDeviceCount() <= 0:
        pytest.skip("没有可见 GPU")
    a, b = _unit(48, seed=10), _unit(52, seed=11)
    cpu = matching_gpu.cross_check(a, b, device=None)
    gpu = matching_gpu.cross_check(a, b, device=0)
    assert set(map(tuple, gpu.tolist())) == set(map(tuple, cpu.tolist()))
