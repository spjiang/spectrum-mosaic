import numpy as np
import pytest
from fastapi import HTTPException

from app.services.geodesy import utm_to_wgs84, wgs84_to_utm
from app.services.gps_check import (
    build_report,
    gsd_from_run_log,
    ortho_gsd_of,
    pixel_errors,
    req_05_01_report,
    sample_errors,
    summarize,
)
from app.services.resolution import gsd_m, jpeg_size, needs_resolution_choice


def test_utm_roundtrip_matches_rasterio_reference():
    x, y = wgs84_to_utm(22.55, 114.05, zone=50, south=False)
    assert x == pytest.approx(196591.3705319548, abs=0.05)
    assert y == pytest.approx(2496703.4877804234, abs=0.05)
    lat, lon = utm_to_wgs84(x, y, zone=50, south=False)
    assert lat == pytest.approx(22.55, abs=1e-6)
    assert lon == pytest.approx(114.05, abs=1e-6)


def test_profile_scale_without_dsm_gsd_needs_resolution():
    """API 只带模版、没带倍数时，也要把倍数写成 GSD，否则格网会随自标定焦距漂。"""
    assert needs_resolution_choice({}, {"ortho_scale": 1}) is True
    assert needs_resolution_choice({}, {"ortho_scale": 1, "dsm_gsd": 0.093}) is False
    assert needs_resolution_choice({"ortho_scale": 2}, {"ortho_scale": 1, "dsm_gsd": 0.093}) is True
    assert needs_resolution_choice({}, {}) is False
    assert needs_resolution_choice({}, {"ortho_scale": 1, "grid_reference": "/ref"}) is False


def test_ortho_gsd_uses_the_run_log_when_the_job_has_none():
    text = "处理记录：原始分辨率 0.044163 m/px，拼图分辨率 0.046620 m/px，DSM 0.093241 m"
    assert gsd_from_run_log(text) == pytest.approx(0.046620)
    assert ortho_gsd_of(None, {"dsm_gsd": None}, text) == pytest.approx(0.046620)
    with pytest.raises(HTTPException):
        ortho_gsd_of(None, {}, "")


def test_gsd_is_height_over_scaled_focal():
    # 1508.19 px @ 1440 宽，像宽 2048 时焦距按比例放大
    got = gsd_m(80.0, 2048)
    focal = 1508.19 * (2048 / 1440)
    assert got == pytest.approx(80.0 / focal)


def _color_jpeg(width: int, height: int, agl: float, lat: float, lon: float) -> bytes:
    # FFD8 + SOF0。解析器在标记后第 5 字节读高、第 7 字节读宽。
    sof = bytes.fromhex("ffd8ffc0000b08") + height.to_bytes(2, "big") + width.to_bytes(2, "big")
    xmp = (
        f"<Camera:AboveGroundAltitude>{agl}</Camera:AboveGroundAltitude>"
        f"<Camera:PositionLatitudeFused>{lat}</Camera:PositionLatitudeFused>"
        f"<Camera:PositionLongitudeFused>{lon}</Camera:PositionLongitudeFused>"
    ).encode("ascii")
    return sof + xmp


def test_scene_profile_uses_image_size_bands_and_track(tmp_path):
    from app.services.resolution import measure_native_gsd

    (tmp_path / "MAX_1_Color_D.jpg").write_bytes(_color_jpeg(2048, 1536, 80.0, 22.50, 114.00))
    (tmp_path / "MAX_2_Color_D.jpg").write_bytes(_color_jpeg(2048, 1536, 80.0, 22.51, 114.01))
    (tmp_path / "MAX_1_550nm_D.tif").write_bytes(b"")
    (tmp_path / "MAX_1_850nm_D.tif").write_bytes(b"")
    (tmp_path / "MAX_2_720nm_R.tif").write_bytes(b"")
    (tmp_path / "MAX_1_Color_W.jpg").write_bytes(_color_jpeg(2048, 1536, 80.0, 0.0, 0.0))
    info = measure_native_gsd(tmp_path)
    assert info["width"] == 2048
    assert info["height"] == 1536
    assert info["n_color"] == 2
    assert info["bands"] == ["Color", "550nm", "720nm", "850nm"]
    assert info["n_ms_bands"] == 3
    assert info["span_x_m"] > 2048 * info["native_gsd_m"]
    assert info["span_y_m"] > 1536 * info["native_gsd_m"]


def test_jpeg_size_reads_sof():
    # FFD8 + SOF0：长度 8，精度 8，高 1536，宽 2048
    data = bytes.fromhex("ffd8ffc000080806000800")
    assert jpeg_size(data) == (2048, 1536)
    assert jpeg_size(b"not a jpeg") is None


def test_report_shows_each_point_and_the_pixel_formula():
    pose = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
    gps = np.array([[0.10, 0.0], [10.0, 0.0], [0.0, 10.20]])
    report = build_report(
        pose,
        gps,
        np.array([7, 8, 9]),
        ["a.jpg", "b.jpg", "c.jpg"],
        n=3,
        gsd_m=0.05,
        gsd_source="运行日志",
        crs="EPSG:32650",
        gps_source="检查点",
    )
    by_name = {p["name"]: p for p in report["points"]}
    assert by_name["a.jpg"]["dist_m"] == pytest.approx(0.10)
    assert by_name["a.jpg"]["error_px"] == pytest.approx(2.0)
    assert by_name["a.jpg"]["over"] is False
    assert by_name["c.jpg"]["error_px"] == pytest.approx(4.0)
    assert by_name["c.jpg"]["over"] is True
    assert report["n_over"] == 1
    assert report["n_valid"] == 3
    assert report["max_px"] == pytest.approx(4.0)
    assert report["worst_name"] == "c.jpg"
    assert report["threshold_m"] == pytest.approx(0.10)
    assert report["rms_px"] == pytest.approx((20.0 / 3) ** 0.5)


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


def test_req_05_01_does_not_treat_camera_gnss_as_field_truth():
    report = req_05_01_report()
    assert report["requirement"] == "REQ-05-01"
    assert report["accepted"] is False
    assert "野外真实 GPS 真值" in report["message"]
    assert "不具备验收条件" in report["message"]
    by_no = {clause["no"]: clause for clause in report["clauses"]}
    assert by_no["1"]["text"] == "每一个光谱波段均匀选取 20~30 个检测点位"
    assert by_no["1"]["result"] == "未执行"
    assert by_no["2"]["text"] == "与野外真实 GPS 真值坐标比对"
    assert by_no["2"]["result"] == "未执行"
    assert by_no["3"]["text"].startswith("平均位置误差严格小于 2 像素")
    assert by_no["3"]["result"] == "不能判定"
    assert by_no["4"]["result"] == "需求未给定"
