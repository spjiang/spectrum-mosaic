import numpy as np
import pytest

from ms_mosaic.xp import array_module, asnumpy, on_device


def test_cpu_module_is_numpy():
    assert array_module(None) is np


def test_asnumpy_passes_numpy_through():
    a = np.arange(4.0)
    assert asnumpy(a) is a


def test_on_device_none_is_noop():
    with on_device(None):
        assert array_module(None) is np


@pytest.mark.gpu
def test_gpu_round_trip():
    import cupy

    xp = array_module(0)
    assert xp is cupy
    with on_device(0):
        a = xp.arange(6.0).reshape(2, 3)
        b = asnumpy(a * 2)
    assert isinstance(b, np.ndarray)
    np.testing.assert_array_equal(b, np.arange(6.0).reshape(2, 3) * 2)
