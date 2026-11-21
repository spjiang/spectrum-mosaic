"""相机适配：MAX 与 Wayho 两种目录读成同一套曝光。"""

from pathlib import Path

import numpy as np
import pytest

from ms_mosaic.camera import FOCAL_OVER_WIDTH_RGB, Camera
from ms_mosaic.scene import build_block, tilt_deg, usable_shots
from ms_mosaic.sources import (
    MAX_MS,
    WAYHO_RGB,
    detect_source,
    normalize_source,
    scan_input,
    wayho_pixel_um,
)

_REAL_WAYHO = Path("/data/input/rgb1")


def _xmp(*, rig: str, lat: float, lon: float, yaw: float, agl: float = 124.0, alt: float | None = None) -> bytes:
    alt_line = f"<Camera:PositionAltitudeFused>{alt}</Camera:PositionAltitudeFused>" if alt is not None else ""
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
        'xmlns:Camera="http://pix4d.com/camera/1.0">'
        f"<Camera:PrincipalPoint>32.0,24.0</Camera:PrincipalPoint>"
        f"<Camera:PerspectiveFocalLength>12.000000</Camera:PerspectiveFocalLength>"
        f"<Camera:RigName>{rig}</Camera:RigName>"
        f"<Camera:Yaw>{yaw}</Camera:Yaw><Camera:Pitch>0.000000</Camera:Pitch><Camera:Roll>0.000000</Camera:Roll>"
        f"<Camera:AboveGroundAltitude>{agl}</Camera:AboveGroundAltitude>"
        f"<Camera:PositionLatitudeFused>{lat}</Camera:PositionLatitudeFused>"
        f"<Camera:PositionLongitudeFused>{lon}</Camera:PositionLongitudeFused>"
        f"{alt_line}"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode()


def _write_wayho(path: Path, *, rig: str = "VEN-1220-32U3C-FPC-M05", make: str = "Wayho", **xmp) -> None:
    import tifffile

    blob = _xmp(rig=rig, **xmp)
    tifffile.imwrite(
        path,
        np.zeros((48, 64, 3), np.uint8),
        photometric="rgb",
        extratags=[(271, "s", 0, make, True), (700, "B", len(blob), blob, True)],
    )


def _wayho_dir(tmp_path: Path, n: int = 3, **kw) -> Path:
    root = tmp_path / "rgb"
    root.mkdir()
    for i in range(n):
        _write_wayho(root / f"{i:06d}.tif", lat=22.7909 + i * 1e-5, lon=114.0691, yaw=-99.5, **kw)
    return root


def test_detect_wayho_by_rig_name(tmp_path: Path):
    assert detect_source(_wayho_dir(tmp_path)) == WAYHO_RGB


def test_detect_max_by_file_name(tmp_path: Path):
    (tmp_path / "MAX_0001_Color_D.jpg").write_bytes(b"")
    assert detect_source(tmp_path) == MAX_MS


def test_detect_unknown_numbered_camera_asks_for_type(tmp_path: Path):
    root = tmp_path / "x"
    root.mkdir()
    _write_wayho(root / "000000.tif", rig="OTHER-CAM", make="Acme", lat=22.79, lon=114.07, yaw=0.0)
    with pytest.raises(ValueError, match=WAYHO_RGB):
        detect_source(root)


def test_detect_empty_dir_lists_supported_naming(tmp_path: Path):
    with pytest.raises(ValueError, match="MAX_<编号>"):
        detect_source(tmp_path)


def test_normalize_source_rejects_unknown():
    assert normalize_source(None) == "auto"
    assert normalize_source(" Wayho_RGB ") == WAYHO_RGB
    with pytest.raises(ValueError, match="相机类型"):
        normalize_source("dji")


def test_wayho_scan_is_nadir_relative_height_and_model_focal(tmp_path: Path):
    """俯仰/横滚写 0 是正下视；没有绝对高程时相机高取相对航高；焦距按型号像元换算。"""
    scan = scan_input(_wayho_dir(tmp_path))
    assert scan.source == WAYHO_RGB and scan.detected
    assert sorted(scan.shots) == [0, 1, 2]
    shot = scan.shots[0]
    assert shot.ms == {}
    assert shot.rgb.band == "Color" and shot.rgb.kind == "rgb"
    assert shot.rgb.camera_model == "VEN-1220-32U3C-FPC-M05"
    assert shot.rgb.focal_px == pytest.approx(12.0 * 1000.0 / 1.85)
    pos = shot.pos
    assert pos.pitch_deg == pytest.approx(-90.0)
    assert tilt_deg(pos.pitch_deg, pos.roll_deg) == pytest.approx(0.0, abs=1e-9)
    assert pos.alt_relative and pos.alt_m == pytest.approx(124.0)
    assert any("相对起飞点" in note for note in scan.notes)
    assert any("6486 px" in note for note in scan.notes)


def test_wayho_keeps_absolute_altitude_when_present(tmp_path: Path):
    scan = scan_input(_wayho_dir(tmp_path, alt=210.5))
    pos = scan.shots[0].pos
    assert not pos.alt_relative and pos.alt_m == pytest.approx(210.5)
    assert not any("相对起飞点" in note for note in scan.notes)


def test_pixel_size_override_and_unknown_model(tmp_path: Path):
    root = tmp_path / "x"
    root.mkdir()
    _write_wayho(root / "000000.tif", rig="VEN-9999", lat=22.79, lon=114.07, yaw=0.0)
    unknown = scan_input(root, camera_source=WAYHO_RGB)
    assert unknown.shots[0].rgb.focal_px is None
    assert any("pixel_size_um" in note for note in unknown.notes)

    given = scan_input(root, camera_source=WAYHO_RGB, pixel_size_um=2.4)
    assert given.shots[0].rgb.focal_px == pytest.approx(5000.0)
    assert any("处理方案" in note for note in given.notes)


def test_wayho_pixel_um_prefers_override_then_model_then_exif():
    assert wayho_pixel_um("VEN-1220-32U3C", 400.0, 3.0) == (3.0, "处理方案")
    assert wayho_pixel_um("ven-1220-32u3c", 400.0, None)[0] == pytest.approx(1.85)
    assert wayho_pixel_um("VEN-0000", 400.0, None) == (pytest.approx(2.5), "EXIF 焦平面分辨率")
    assert wayho_pixel_um("", None, None) == (None, "")


def test_max_index_limits_wayho_scan(tmp_path: Path):
    scan = scan_input(_wayho_dir(tmp_path, n=4), max_index=1)
    assert sorted(scan.shots) == [0, 1]


def test_forcing_wrong_source_reports_empty(tmp_path: Path):
    with pytest.raises(ValueError, match="MAX 多光谱相机"):
        scan_input(_wayho_dir(tmp_path), camera_source=MAX_MS)


def test_build_block_uses_adapter_focal_and_model(tmp_path: Path):
    shots = scan_input(_wayho_dir(tmp_path)).shots
    keep, reasons = usable_shots(shots)
    assert reasons["high_tilt"] == 0 and len(keep) == 3
    block = build_block(keep)
    cam = block.cameras["Color"]
    assert cam.f == pytest.approx(12.0 * 1000.0 / 1.85)
    assert cam.model == "VEN-1220-32U3C-FPC-M05"
    assert block.band_order() == ("Color",)
    assert block.ground_z == pytest.approx(0.0)
    axis = block.poses[0].rotation[2]
    assert axis[2] == pytest.approx(-1.0)


def test_camera_initial_falls_back_to_ratio():
    assert Camera.initial("Color", 4000, 3000, kind="rgb").f == pytest.approx(FOCAL_OVER_WIDTH_RGB * 4000)
    assert Camera.initial("Color", 4000, 3000, kind="rgb", f=None).model == "MAX-S800"
    assert Camera.initial("Color", 4000, 3000, kind="rgb", f=6500.0, model="VEN").f == 6500.0
    assert Camera.initial("Color", 4000, 3000, kind="rgb", f=float("nan")).f == pytest.approx(
        FOCAL_OVER_WIDTH_RGB * 4000
    )


@pytest.mark.skipif(not _REAL_WAYHO.is_dir(), reason="本机没有 rgb1 样例")
def test_real_wayho_dataset():
    scan = scan_input(_REAL_WAYHO)
    assert scan.source == WAYHO_RGB
    assert len(scan.shots) == 45
    keep, reasons = usable_shots(scan.shots)
    assert len(keep) == 45, reasons
    block = build_block(keep)
    assert block.cameras["Color"].width == 4024 and block.cameras["Color"].height == 3036
    assert block.ground_z == pytest.approx(0.0)
