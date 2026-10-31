"""波段表来自文件名，不写死 S810 的 8 个波长。"""

from pathlib import Path

from ms_mosaic.catalog import FileRecord, Pos, Shot
from ms_mosaic.compose import ms_cache_per_band
from ms_mosaic.scene import BAND_ORDER, build_block, ordered_bands, resolve_wanted


def test_ordered_bands_puts_color_first_then_wavelength():
    assert ordered_bands(["850nm", "Color", "510nm", "450nm"]) == (
        "Color",
        "450nm",
        "510nm",
        "850nm",
    )
    dozens = [f"{1000 - 10 * i}nm" for i in range(30)]
    order = ordered_bands(dozens)
    assert len(order) == 30
    assert order[0] == "710nm"
    assert order[-1] == "1000nm"


def test_default_eight_names_do_not_drop_extra_wavelengths():
    found = ("Color", "450nm", "510nm", "550nm", "850nm")
    assert resolve_wanted(None, found) == found
    assert resolve_wanted(BAND_ORDER, found) == found
    assert resolve_wanted(["Color"], found) == ("Color",)
    assert resolve_wanted(["850nm", "510nm"], found) == ("510nm", "850nm")


def test_build_block_keeps_wavelengths_outside_s810(monkeypatch):
    monkeypatch.setattr("ms_mosaic.scene.image_size", lambda _path: (8, 6))
    image = Path("frame.jpg")
    pos = Pos(
        lon=114.05,
        lat=22.54,
        alt_m=120.0,
        yaw_deg=10.0,
        pitch_deg=-89.0,
        roll_deg=0.2,
        agl_m=90.0,
    )

    def record(band: str) -> FileRecord:
        return FileRecord(
            path=image,
            index=1,
            band=band,
            role="D",
            kind="rgb" if band == "Color" else "ms",
            wavelength_nm=None if band == "Color" else int(band[:-2]),
            pos=pos,
        )

    shot = Shot(index=1, rgb=record("Color"), pos=pos, role="D")
    shot.ms["1200nm"] = record("1200nm")
    shot.ms["510nm"] = record("510nm")
    shot.ms["850nm"] = record("850nm")
    block = build_block({1: shot})
    assert block.band_order() == ("Color", "510nm", "850nm", "1200nm")
    assert [im.group for im in block.images] == [0, 1, 2, 3]
    assert set(block.cameras) == {"Color", "510nm", "850nm", "1200nm"}


def test_ms_image_cache_stays_bounded_for_dozens_of_bands():
    assert ms_cache_per_band(7) == 24
    assert ms_cache_per_band(8) == 24
    assert ms_cache_per_band(30) == 6
    assert ms_cache_per_band(30) * 30 <= 24 * 8
