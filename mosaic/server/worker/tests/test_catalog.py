from pathlib import Path

import pytest

from ms_mosaic.catalog import Shot, group_shots, parse_filename, read_sidecar, scan_directory
from ms_mosaic.local_defaults import INPUT as REAL

# 容器内挂载的 S810 辐射校正样例（与原始同曝光，XMP 无 Fused、有 EXIF GPS）
_R_SAMPLE = Path(
    "/data/input/S810原始数据/20260528原始数据/辐射校正数据/15：30/MAX_0002_450nm_R.tif"
)
_D_SAMPLE = Path(
    "/data/input/S810原始数据/20260528原始数据/原始数据/15：30/MAX_20260528_002/MAX_0002_450nm_D.tif"
)


def test_parse_color_jpg():
    rec = parse_filename(Path("MAX_0003_Color_D.jpg"))
    assert rec.index == 3
    assert rec.band == "Color"
    assert rec.role == "D"
    assert rec.kind == "rgb"


def test_parse_green_tif():
    rec = parse_filename(Path("MAX_0010_550nm_D.tif"))
    assert rec.index == 10
    assert rec.band == "550nm"
    assert rec.wavelength_nm == 550
    assert rec.role == "D"
    assert rec.kind == "ms"


def test_parse_radiometric_r():
    """辐射校正产品后缀是 _R，必须能入册。"""
    rec = parse_filename(Path("MAX_0002_450nm_R.tif"))
    assert rec is not None
    assert rec.index == 2
    assert rec.band == "450nm"
    assert rec.role == "R"
    assert rec.kind == "ms"


def test_parse_wavelength_is_not_limited_to_s810():
    """文件名里的整数波长都入册，不限于 450–850 nm。"""
    rec = parse_filename(Path("MAX_0008_510nm_D.tif"))
    assert rec is not None
    assert rec.band == "510nm"
    assert rec.wavelength_nm == 510
    rec = parse_filename(Path("MAX_0008_1200nm_R.tif"))
    assert rec is not None and rec.band == "1200nm" and rec.role == "R"


def test_parse_white_panel():
    rec = parse_filename(Path("MAX_0002_450nm_W.tif"))
    assert rec.role == "W"
    assert rec.kind == "ms"


def test_parse_ignores_zip():
    assert parse_filename(Path("MAX_0010_Color_D.zip")) is None


def test_group_shots_binds_rgb_and_bands():
    files = [
        parse_filename(Path("MAX_0003_Color_D.jpg")),
        parse_filename(Path("MAX_0003_450nm_D.tif")),
        parse_filename(Path("MAX_0003_550nm_D.tif")),
        parse_filename(Path("MAX_0004_Color_D.jpg")),
    ]
    shots = group_shots(files)
    assert set(shots) == {3, 4}
    assert shots[3].rgb.path.name == "MAX_0003_Color_D.jpg"
    assert "550nm" in shots[3].ms
    assert shots[4].ms == {}


def test_group_shots_radiometric_is_not_whiteboard():
    files = [
        parse_filename(Path("MAX_0002_450nm_R.tif")),
        parse_filename(Path("MAX_0002_550nm_R.tif")),
    ]
    shots = group_shots(files)
    assert shots[2].role != "W"
    assert "450nm" in shots[2].ms


@pytest.mark.skipif(not _R_SAMPLE.is_file(), reason="no S810 radiometric sample")
def test_r_sidecar_uses_exif_gps_keeps_xmp_attitude():
    """_R 无 Position*Fused，位置走 EXIF GPS；Yaw/Pitch/相对高仍取 XMP。"""
    pos, uuid = read_sidecar(_R_SAMPLE)
    assert pos is not None
    assert uuid == "20265286f345915"
    assert abs(pos.lat - 22.71191089) < 1e-5
    assert abs(pos.lon - 114.411399) < 1e-5
    assert abs(pos.alt_m - 129.86) < 0.1
    assert abs(pos.yaw_deg - 51.1) < 0.05
    assert abs(pos.pitch_deg - (-90.1)) < 0.05
    assert abs(pos.agl_m - 100.1) < 0.05
    assert pos.agl_m > 50  # 不能再被当成贴地


@pytest.mark.skipif(not (_R_SAMPLE.is_file() and _D_SAMPLE.is_file()), reason="no S810 pair")
def test_r_and_d_sidecar_agree_on_position():
    pos_r, _ = read_sidecar(_R_SAMPLE)
    pos_d, _ = read_sidecar(_D_SAMPLE)
    assert pos_r is not None and pos_d is not None
    assert abs(pos_r.lat - pos_d.lat) < 1e-4
    assert abs(pos_r.lon - pos_d.lon) < 1e-4
    assert abs(pos_r.alt_m - pos_d.alt_m) < 0.2
    assert abs(pos_r.yaw_deg - pos_d.yaw_deg) < 0.05


@pytest.mark.skipif(not REAL.exists(), reason="no MAX_20251017_001 dataset")
def test_scan_real_dir_reads_pos_and_uuid():
    shots = scan_directory(REAL, max_index=3)
    assert 1 in shots
    assert shots[1].rgb is not None
    assert shots[1].pos is not None
    assert shots[1].pos.lon > 100
    assert shots[1].pos.lat > 23
    shot3 = shots[3]
    assert shot3.pos is not None
    assert shot3.pos.agl_m > 50
    assert abs(shot3.pos.yaw_deg - 80.6) < 0.5
    assert "550nm" in shot3.ms
    assert shot3.capture_uuid
    assert shot3.ms["450nm"].capture_uuid == shot3.capture_uuid


def test_usable_shots_respects_agl_tilt_and_whiteboard():
    from ms_mosaic.catalog import Pos, Shot
    from ms_mosaic.scene import usable_shots

    def shot(idx, *, role="D", agl=100.0, pitch=-90.0, has_pos=True):
        pos = (
            Pos(lon=100.0, lat=24.0, alt_m=1800.0, yaw_deg=0.0, pitch_deg=pitch, roll_deg=0.0, agl_m=agl)
            if has_pos
            else None
        )
        return Shot(index=idx, pos=pos, role=role)

    shots = {
        1: shot(1, role="W"),
        2: shot(2, agl=1.0),
        3: shot(3, pitch=0.0),
        4: shot(4, has_pos=False),
        5: shot(5),
    }
    keep, reasons = usable_shots(shots)
    assert set(keep) == {5}
    assert reasons["white_panel"] == 1
    assert reasons["low_agl"] == 1
    assert reasons["high_tilt"] == 1
    assert reasons["no_pos"] == 1

    keep_w, _ = usable_shots(shots, drop_white_panel=False, min_agl_m=0.0, max_tilt_deg=180.0)
    assert 1 in keep_w
    assert 5 in keep_w
    assert 4 not in keep_w
