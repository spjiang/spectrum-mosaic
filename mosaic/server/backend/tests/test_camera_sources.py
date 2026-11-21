"""提交前识别相机类型，并按相机估原始分辨率。"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from app.services.camera_sources import MAX_MS, WAYHO_RGB, detect_source, focal_px_for, normalize_source
from app.services.params import validate_param_values
from app.services.resolution import gsd_m, measure_native_gsd


def _xmp(*, rig: str, agl: float, lat: float, lon: float) -> bytes:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
        'xmlns:Camera="http://pix4d.com/camera/1.0">'
        "<Camera:PerspectiveFocalLength>12.000000</Camera:PerspectiveFocalLength>"
        f"<Camera:RigName>{rig}</Camera:RigName>"
        f"<Camera:AboveGroundAltitude>{agl}</Camera:AboveGroundAltitude>"
        f"<Camera:PositionLatitudeFused>{lat}</Camera:PositionLatitudeFused>"
        f"<Camera:PositionLongitudeFused>{lon}</Camera:PositionLongitudeFused>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    ).encode()


def _wayho_dir(tmp_path: Path, *, rig: str = "VEN-1220-32U3C-FPC-M05", n: int = 3) -> Path:
    import tifffile

    root = tmp_path / "rgb1"
    root.mkdir(parents=True)
    for i in range(n):
        blob = _xmp(rig=rig, agl=120.0, lat=22.79 + i * 1e-4, lon=114.07)
        tifffile.imwrite(
            root / f"{i:06d}.tif",
            np.zeros((30, 40, 3), np.uint8),
            photometric="rgb",
            extratags=[(271, "s", 0, "Wayho", True), (700, "B", len(blob), blob, True)],
        )
    return root


def test_detect_source_by_names_and_model(tmp_path: Path):
    assert detect_source([Path("MAX_0001_Color_D.jpg")]) == MAX_MS
    root = _wayho_dir(tmp_path)
    assert detect_source(sorted(root.iterdir())) == WAYHO_RGB
    with pytest.raises(ValueError, match="MAX_<编号>"):
        detect_source([Path("notes.txt")])


def test_focal_px_from_model_table_or_override():
    xmp = _xmp(rig="VEN-1220-32U3C", agl=1.0, lat=0.0, lon=0.0).decode()
    assert focal_px_for(xmp) == pytest.approx(12000 / 1.85)
    assert focal_px_for(xmp, 2.4) == pytest.approx(5000.0)
    other = _xmp(rig="VEN-0000", agl=1.0, lat=0.0, lon=0.0).decode()
    assert focal_px_for(other) is None


def test_normalize_source():
    assert normalize_source(None) == "auto"
    with pytest.raises(ValueError):
        normalize_source("dji")


def test_wayho_native_gsd_uses_focal_from_model(tmp_path: Path):
    info = measure_native_gsd(_wayho_dir(tmp_path))
    assert info["camera_source"] == WAYHO_RGB and info["camera_detected"] is True
    assert info["camera_label"] == "Wayho VEN 系列 RGB 相机"
    assert info["native_gsd_m"] == pytest.approx(120.0 / (12000 / 1.85))
    assert (info["width"], info["height"], info["n_color"]) == (40, 30, 3)
    assert info["bands"] == ["Color"] and info["n_ms_bands"] == 0


def test_wayho_resolution_left_to_worker(tmp_path: Path, monkeypatch):
    """Wayho 只有相对航高，提交时只留倍数，DSM/正射 GSD 由空三后定。"""
    from app.services import resolution

    root = _wayho_dir(tmp_path)
    monkeypatch.setattr(resolution, "resolve_input_dir", lambda raw, settings: Path(raw))
    assert measure_native_gsd(root)["resolution_after_at"] is True
    snap = {"ortho_scale": 2, "dsm_gsd": 0.07, "ortho_gsd_m": 0.035, "native_gsd_m": 0.0185}
    resolution.apply_resolution_choice(snap, str(root), settings=None)
    assert snap == {"ortho_scale": 2}


def test_max_resolution_still_locked_at_submit(tmp_path: Path, monkeypatch):
    from app.services import resolution
    from test_resolution_and_gps import _color_jpeg

    (tmp_path / "MAX_1_Color_D.jpg").write_bytes(_color_jpeg(1440, 1080, 80.0, 22.50, 114.00))
    monkeypatch.setattr(resolution, "resolve_input_dir", lambda raw, settings: Path(raw))
    assert measure_native_gsd(tmp_path)["resolution_after_at"] is False
    snap = {"ortho_scale": 1}
    resolution.apply_resolution_choice(snap, str(tmp_path), settings=None)
    assert snap["native_gsd_m"] == pytest.approx(80.0 / 1508.19)
    assert snap["dsm_gsd"] == pytest.approx(2 * 80.0 / 1508.19)


def test_wayho_unknown_model_falls_back_to_ratio(tmp_path: Path):
    info = measure_native_gsd(_wayho_dir(tmp_path, rig="VEN-0000"), camera_source=WAYHO_RGB)
    assert info["camera_detected"] is False
    assert info["native_gsd_m"] == pytest.approx(gsd_m(120.0, 40))
    info = measure_native_gsd(_wayho_dir(tmp_path / "b", rig="VEN-0000"), pixel_size_um=2.4)
    assert info["native_gsd_m"] == pytest.approx(120.0 / 5000.0)


def test_unrecognized_dir_is_400(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(HTTPException) as err:
        measure_native_gsd(tmp_path)
    assert err.value.status_code == 400


def _d(key: str, value_type: str):
    return SimpleNamespace(key=key, value_type=value_type, required=False, required_when=None)


def test_param_validation_for_camera_fields():
    defs = [_d("camera_source", "enum"), _d("pixel_size_um", "float")]
    assert validate_param_values(defs, {"camera_source": "wayho_rgb", "pixel_size_um": 1.85}) == []
    assert validate_param_values(defs, {"camera_source": "auto", "pixel_size_um": None}) == []
    errs = validate_param_values(defs, {"camera_source": "dji", "pixel_size_um": 100})
    assert any("相机类型" in e for e in errs) and any("像元尺寸" in e for e in errs)
