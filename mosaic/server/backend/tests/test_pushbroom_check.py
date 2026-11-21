"""推扫架次提交前核对：三件套、头文件、字节数、定位表头。"""

import os
from pathlib import Path

import pytest

from app.services.pushbroom_check import find_flights, parse_envi_header, quick_check

GPS_HEADER = (
    "Lines,UnixTime,Microseconds,Latitude,Longitude,Altitude,W,X,Y,Z,"
    "XVelocity,YVelocity,ZVelocity,UAVTimeMs,SysUnixTime,SysMicroseconds"
)


def _hdr(samples=4, lines=3, bands=2, **extra) -> str:
    fields = {
        "samples": samples,
        "lines": lines,
        "bands": bands,
        "header offset": 0,
        "data type": 12,
        "interleave": "bil",
        "byte order": 0,
    }
    fields.update(extra)
    body = "\n".join(f"{k} = {v}" for k, v in fields.items())
    waves = ",\n".join(str(400 + i) for i in range(bands))
    return f"ENVI\ndescription = {{File Imported into ENVI.}}\n{body}\nwavelength = {{{waves}}}\n"


def _flight(root: Path, name="FigSpec_1", *, samples=4, lines=3, bands=2, spe_bytes=None, hdr=None, gps=None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "figspec.hdr").write_text(hdr if hdr is not None else _hdr(samples, lines, bands))
    size = samples * lines * bands * 2 if spe_bytes is None else spe_bytes
    (d / "figspec.spe").write_bytes(b"\0" * size)
    rows = gps if gps is not None else f"{GPS_HEADER}\n1,100,0,35.1,113.7,142,0,0,0,0,0,0,0,0,0,0\n3,102,0,35.1,113.7,142,0,0,0,0,0,0,0,0,0,0\n"
    (d / "figspec.gps").write_text(rows)
    return d


def test_good_flight_passes_and_reports_layout(tmp_path):
    res = quick_check(_flight(tmp_path))
    assert res.ok, res.errors
    assert res.flight["samples"] == 4
    assert res.flight["lines"] == 3
    assert res.flight["bands"] == 2
    assert res.flight["gps_records"] == 2
    assert res.flight["duration_s"] == 2
    assert res.flight["wavelength_min"] == 400.0


def test_flight_start_comes_from_first_gps_record(tmp_path):
    result = quick_check(_flight(tmp_path))
    # 第一条定位 UnixTime = 100
    assert result.flight["started_at"] == "1970-01-01T00:01:40+00:00"


def test_multiline_braces_are_joined():
    hdr = parse_envi_header("ENVI\nwavelength = {1,\n2,\n3}\nsamples = 5\n")
    assert hdr["wavelength"].replace(" ", "") == "{1,2,3}"
    assert hdr["samples"] == "5"


def test_truncated_cube_is_rejected(tmp_path):
    res = quick_check(_flight(tmp_path, spe_bytes=10))
    assert not res.ok
    assert any("没拷完" in e for e in res.errors)


def test_mac_resource_fork_header_is_rejected(tmp_path):
    fork = "\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        "
    res = quick_check(_flight(tmp_path, hdr=fork))
    assert not res.ok
    assert any("不是有效的 ENVI 头文件" in e for e in res.errors)


def test_gps_with_log_text_on_top_is_rejected(tmp_path):
    gps = "Best parameters: {'max_depth': 10}\nR2: 0.75\n"
    res = quick_check(_flight(tmp_path, gps=gps))
    assert not res.ok
    assert any("第一行不是定位表头" in e for e in res.errors)


def test_missing_file_is_reported(tmp_path):
    d = _flight(tmp_path)
    (d / "figspec.gps").unlink()
    res = quick_check(d)
    assert res.errors == ["缺少文件：figspec.gps"]


def test_unsupported_layout_is_rejected(tmp_path):
    res = quick_check(_flight(tmp_path, hdr=_hdr(interleave="bsq", **{"data type": 4})))
    assert any("BIL" in e for e in res.errors)
    assert any("data type" in e for e in res.errors)


def test_wavelength_count_must_match_bands(tmp_path):
    hdr = _hdr(bands=2).replace("wavelength = {400,\n401}", "wavelength = {400}")
    res = quick_check(_flight(tmp_path, hdr=hdr))
    assert any("wavelength 有 1 个" in e for e in res.errors)


def test_find_flights_walks_two_levels_and_skips_hidden(tmp_path):
    _flight(tmp_path, "a")
    _flight(tmp_path / "survey", "b")
    _flight(tmp_path / ".trash", "c")
    names = [p.name for p in find_flights(tmp_path)]
    assert names == ["a", "b"]


REAL = os.environ.get("PUSHBROOM_REAL_DATA")


@pytest.mark.skipif(not REAL, reason="未挂载真实架次（PUSHBROOM_REAL_DATA）")
@pytest.mark.parametrize(
    "name",
    ["FigSpec_260414_134505", "FigSpec_260419_132008", "FigSpec_260429_134344", "FigSpec_260429_135339"],
)
def test_real_flights_pass(name):
    res = quick_check(Path(REAL) / name)
    assert res.ok, res.errors
    assert res.flight["samples"] == 960
    assert res.flight["bands"] == 600


@pytest.mark.skipif(not REAL, reason="未挂载真实架次（PUSHBROOM_REAL_DATA）")
def test_real_broken_flight_is_rejected():
    res = quick_check(Path(REAL) / "FigSpec_260410_142436")
    assert not res.ok
    assert len(res.errors) == 2
