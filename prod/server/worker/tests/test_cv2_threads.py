"""OpenCV 自带线程池不受 OMP_NUM_THREADS 约束，必须在进程入口显式关掉。"""

import pytest

cv2 = pytest.importorskip("cv2")

from ms_mosaic.runner import _limit_cv2_threads


def test_limit_cv2_threads_is_one():
    cv2.setNumThreads(8)
    _limit_cv2_threads()
    n = cv2.getNumThreads()
    # OpenCV 约定 0 表示串行；1 是单线程。两者都不再超订。
    assert n in (0, 1)
