import numpy as np
import pytest
from fastapi import HTTPException

from app.services.geodesy import utm_to_wgs84, wgs84_to_utm
from app.services.gps_check import pixel_errors, sample_errors, summarize
from app.services.resolution import gsd_m, jpeg_size


def test_utm_roundtrip_matches_rasterio_reference():
    x, y = wgs84_to_utm(22.55, 114.05, zone=50, south=False)
    assert x == pytest.approx(196591.3705319548, abs=0.05)
    assert y == pytest.approx(2496703.4877804234, abs=0.05)
    lat, lon = utm_to_wgs84(x, y, zone=50, south=False)
    assert lat == pytest.approx(22.55, abs=1e-6)
    assert lon == pytest.approx(114.05, abs=1e-6)


def test_gsd_is_height_over_scaled_focal():
    # 1508.19 px @ 1440 宽，像宽 2048 时焦距按比例放大
    got = gsd_m(80.0, 2048)
    focal = 1508.19 * (2048 / 1440)
    assert got == pytest.approx(80.0 / focal)


def test_jpeg_size_reads_sof():
    # FFD8 + SOF0：长度 8，精度 8，高 1536，宽 2048
    data = bytes.fromhex("ffd8ffc000080806000800")
    assert jpeg_size(data) == (2048, 1536)
    assert jpeg_size(b"not a jpeg") is None


def test_pixel_error_is_metres_over_gsd():
    solved = np.array([[0.0, 0.0], [1.0, 0.0]])
    gps = np.array([[0.10, 0.0], [1.0, 0.0]])
    err = pixel_errors(solved, gps, 0.05)
    assert err[0] == pytest.approx(2.0)
    assert err[1] == pytest.approx(0.0)


def test_sample_is_stable_and_threshold_is_two_pixels():
    errors = np.arange(100, dtype=float)
    first = sample_errors(errors, 20)
    second = sample_errors(errors, 20)
    np.testing.assert_array_equal(first, second)
    assert first.size == 20
    report = summarize(np.array([0.5, 1.5, 2.0, 2.1]), n=20, gsd_m=0.05)
    assert report["n_used"] == 4
    assert report["threshold_px"] == 2.0
    assert report["within_tolerance"] is False
    assert report["n_over"] == 1
    ok = summarize(np.array([0.4, 1.9]), n=20, gsd_m=0.05)
    assert ok["within_tolerance"] is True


def test_pixel_error_rejects_nonpositive_gsd():
    with pytest.raises(HTTPException):
        pixel_errors(np.zeros((1, 2)), np.zeros((1, 2)), 0.0)
