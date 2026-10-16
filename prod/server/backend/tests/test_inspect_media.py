from pathlib import Path

import numpy as np
from PIL import Image

from app.services.inspect_media import (
    create_session,
    extract_xmp,
    resolve_band_labels,
    sample_pixel,
)


def test_resolve_band_labels_from_filename_and_xmp():
    ms = resolve_band_labels("MAX_0136_450nm_D.tif", 1)
    assert ms[0]["name"] == "450nm"
    assert ms[0]["wavelength_nm"] == 450
    xmp = resolve_band_labels(
        "shot.tif",
        1,
        [{"ns": "Camera", "name": "BandName", "value": "550"}, {"ns": "Camera", "name": "CentralWavelength", "value": "550"}],
    )
    assert xmp[0]["name"] == "550nm"
    grp = resolve_band_labels("Orthomosaic_pix_surf_group3.tif", 1)
    assert grp[0]["name"] == "650nm"
    rgb = resolve_band_labels("MAX_0001_Color_D.jpg", 3)
    assert [b["name"] for b in rgb] == ["R", "G", "B"]
    rgb_xmp = resolve_band_labels(
        "MAX_0001_Color_D.jpg",
        3,
        [
            {"ns": "Camera", "name": "BandName", "value": "Color"},
            {"ns": "Camera", "name": "CentralWavelength", "value": "520"},
        ],
    )
    assert [b["name"] for b in rgb_xmp] == ["R", "G", "B"]
    dsm = resolve_band_labels("DSM.tif", 1)
    assert dsm[0]["name"] == "DSM"


def test_extract_xmp_tags():
    xml = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description xmlns:Camera="http://www.wayho.com/camera/1.0/"
          Camera:Yaw="12.5">
          <Camera:CaptureUUID>abc-1</Camera:CaptureUUID>
          <Camera:PositionLatitudeFused>23.1</Camera:PositionLatitudeFused>
        </rdf:Description>
      </rdf:RDF>
    </x:xmpmeta>"""
    path = Path("/tmp") / "xmp-only.jpg"
    # JPEG SOI + APP1-ish payload is unnecessary; extract_xmp also scans bytes
    path.write_bytes(b"\xff\xd8" + xml.encode("utf-8") + b"\xff\xd9")
    raw, tags = extract_xmp(path)
    names = {t["name"] for t in tags}
    assert "CaptureUUID" in names
    assert "PositionLatitudeFused" in names
    assert "Yaw" in names
    assert "abc-1" in raw


def test_inspect_jpeg_and_pixel(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.inspect_media.inspect_dir", lambda: tmp_path)
    img = Image.new("RGB", (8, 6), (70, 134, 101))
    img.putpixel((3, 2), (10, 20, 30))
    path = tmp_path / "tiny.jpg"
    img.save(path, format="JPEG", quality=100)
    sess = create_session("tiny.jpg", path.read_bytes())
    meta = sess.meta
    assert meta["format"] == "jpeg"
    assert meta["width"] == 8
    assert meta["height"] == 6
    assert meta["count"] == 3
    assert len(meta["bands"]) == 3
    px = sample_pixel(sess, 3, 2)
    assert px["col"] == 3 and px["row"] == 2
    assert len(px["values"]) == 3
    assert all(isinstance(v, (int, float)) for v in px["values"])


def test_inspect_tiff_xmp_and_bands(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.inspect_media.inspect_dir", lambda: tmp_path)
    import tifffile

    xml = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><Camera:Yaw>1.5</Camera:Yaw></x:xmpmeta>'
    arr = np.arange(24, dtype=np.uint16).reshape(4, 6)
    path = tmp_path / "tiny.tif"
    tifffile.imwrite(path, arr, extratags=[(700, "B", len(xml), xml, True)])
    sess = create_session("MAX_0001_450nm_D.tif", path.read_bytes())
    assert sess.meta["format"] == "tiff"
    assert sess.meta["count"] == 1
    assert sess.meta["bands"][0]["name"] == "450nm"
    assert sess.meta["bands"][0]["wavelength_nm"] == 450
    assert sess.meta["bands"][0]["min"] == 0
    assert sess.meta["bands"][0]["max"] == 23
    names = {t["name"] for t in sess.meta["xmp"]["tags"]}
    assert "Yaw" in names
    px = sample_pixel(sess, 1, 1)
    assert px["values"][0] == 7


def test_large_tiff_samples_overview_and_reads_pixel(tmp_path, monkeypatch):
    import tifffile
    from app.services import inspect_media

    monkeypatch.setattr(inspect_media, "inspect_dir", lambda: tmp_path)
    monkeypatch.setattr(inspect_media, "FULL_LOAD_BYTES", 64)
    monkeypatch.setattr(inspect_media, "PREVIEW_MAX", 8)
    arr = np.arange(12 * 10 * 3, dtype=np.uint8).reshape(12, 10, 3)
    arr[7, 4] = (9, 8, 7)
    path = tmp_path / "src.tif"
    tifffile.imwrite(path, arr, photometric="rgb", rowsperstrip=1)
    sess = create_session("Orthomosaic_pix_surf_group0.tif", path.read_bytes())
    assert sess.lazy is True
    assert sess.array is None
    assert sess.overview is not None
    assert sess.meta["stats_sampled"] is True
    assert sess.meta["width"] == 10
    assert sess.meta["height"] == 12
    assert sess.meta["count"] == 3
    assert [b["name"] for b in sess.meta["bands"][:3]] == ["R", "G", "B"]
    px = sample_pixel(sess, 4, 7)
    assert px["values"] == [9, 8, 7]
