"""CPU / GPU 数组切换。device 为 None 时一律 numpy，CuPy 不会被导入。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import numpy as np


def array_module(device: int | None):
    if device is None:
        return np
    import cupy

    return cupy


def asnumpy(a) -> np.ndarray:
    if isinstance(a, np.ndarray):
        return a
    get = getattr(a, "get", None)
    if callable(get):
        return get()
    return np.asarray(a)


@contextmanager
def on_device(device: int | None) -> Iterator[None]:
    if device is None:
        yield
        return
    import cupy

    with cupy.cuda.Device(device):
        yield
