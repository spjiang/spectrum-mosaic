from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from conftest import real_flight
from synth import SAMPLES, write_flight

from pb_mosaic.ingest.envi import EnviError, parse_header, read_header, write_header
from pb_mosaic.ingest.flight import FlightError, check_flight
from pb_mosaic.ingest.gps import GpsError, parse_gps_text

HDR = """ENVI
description = {File Imported into ENVI.}
samples = 960
lines = 100
bands = 3
header offset = 0
data type = 12
interleave = bil
byte order = 0
default bands = {3,2,1}
wavelength = {400.5,
 500.5,
 600.5}
fwhm = {1,2}
flight height = 120
"""


def test_header_parses_multiline_lists() -> None:
    h = parse_header(HDR)
    assert (h.samples, h.lines, h.bands) == (960, 100, 3)
    assert h.wavelengths == [400.5, 500.5, 600.5]
    assert h.default_bands == [3, 2, 1]
    # fwhm 个数不对时丢弃，而不是报错
    assert h.fwhm == []
    assert h.number("flight height") == 120
    assert h.expected_bytes == 960 * 100 * 3 * 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("ENVI", "NOT ENVI"), "ENVI"),
        (("interleave = bil", "interleave = bsq"), "BIL"),
        (("data type = 12", "data type = 4"), "data type"),
        (("byte order = 0", "byte order = 1"), "小端"),
        (("samples = 960", "samples = 0"), "samples"),
        (("600.5}", "600.5, 700}"), "wavelength"),
        (("lines = 100\n", ""), "lines"),
    ],
)
def test_header_rejects_unsupported(change: tuple[str, str], message: str) -> None:
    with pytest.raises(EnviError, match=message):
        parse_header(HDR.replace(*change, 1))


def test_header_unclosed_brace() -> None:
    with pytest.raises(EnviError, match="花括号"):
        parse_header(HDR + "band names = {a,\n b\n")


def test_header_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "x.hdr"
    write_header(
        p,
        [("samples", 4), ("lines", 2), ("bands", 2), ("data type", 12), ("interleave", "bil"), ("wavelength", [1.5, 2.5])],
    )
    h = read_header(p)
    assert h.wavelengths == [1.5, 2.5]


GPS = """Lines,UnixTime,Microseconds,Latitude,Longitude,Altitude,W,X
119,1776144944,800000,35.13,113.76,142.2,0,1
203,1776144946,800000,35.14,113.77,142.3,0,1
"""


def test_gps_parses() -> None:
    g = parse_gps_text(GPS)
    assert list(g.lines) == [119, 203]
    assert g.time_s[1] - g.time_s[0] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "空"),
        ("a,b,c\n1,2,3\n", "表头"),
        (GPS.replace("142.3", "abc"), "非数字"),
        (GPS.replace("142.3,0,1", "142.3"), "列"),
        (GPS.replace("142.3", "nan"), "NaN"),
        (GPS.replace("203,", "203.5,"), "整数"),
        (GPS.splitlines()[0] + "\n" + GPS.splitlines()[1], "至少"),
    ],
)
def test_gps_rejects(text: str, message: str) -> None:
    with pytest.raises(GpsError, match=message):
        parse_gps_text(text)


def test_check_flight_synth(synth_flight) -> None:
    f = check_flight(synth_flight.root)
    assert f.hdr.samples == SAMPLES
    assert f.line_rate_hz == pytest.approx(70.0)
    s = f.summary()
    assert s["sensor"]["fx model"] == "FS-60C"
    assert s["gps_records"] == len(f.gps)


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def test_check_flight_missing_file(synth_flight, tmp_path: Path) -> None:
    d = _copy(synth_flight.root, tmp_path / "f")
    (d / "figspec.gps").unlink()
    with pytest.raises(FlightError, match="figspec.gps"):
        check_flight(d)


def test_check_flight_truncated_cube(synth_flight, tmp_path: Path) -> None:
    d = _copy(synth_flight.root, tmp_path / "f")
    with (d / "figspec.spe").open("r+b") as fh:
        fh.truncate(1000)
    with pytest.raises(FlightError, match="大小"):
        check_flight(d)


def test_check_flight_gps_beyond_cube(synth_flight, tmp_path: Path) -> None:
    d = _copy(synth_flight.root, tmp_path / "f")
    rows = (d / "figspec.gps").read_text().splitlines()
    last = rows[-1].split(",")
    last[0] = str(int(last[0]) + 100000)
    rows[-1] = ",".join(last)
    (d / "figspec.gps").write_text("\n".join(rows) + "\n")
    with pytest.raises(FlightError, match="超出立方体"):
        check_flight(d)


def test_check_flight_not_monotonic(synth_flight, tmp_path: Path) -> None:
    d = _copy(synth_flight.root, tmp_path / "f")
    rows = (d / "figspec.gps").read_text().splitlines()
    rows[3], rows[4] = rows[4], rows[3]
    (d / "figspec.gps").write_text("\n".join(rows) + "\n")
    with pytest.raises(FlightError, match="递增"):
        check_flight(d)


def test_check_flight_binary_gps(tmp_path: Path) -> None:
    d = write_flight(tmp_path / "f").root
    (d / "figspec.gps").write_bytes(b"\x00\x01\x02" * 100)
    with pytest.raises(FlightError, match="不是文本"):
        check_flight(d)


@pytest.mark.realdata
def test_real_0410_rejected() -> None:
    # 0410 的三件套是 macOS 拷贝留下的 ._ 资源文件
    with pytest.raises(FlightError, match="不是文本"):
        check_flight(real_flight("FigSpec_260410_142436"))


@pytest.mark.realdata
@pytest.mark.parametrize("name", ["FigSpec_260414_134505", "FigSpec_260419_132008", "FigSpec_260429_134344", "FigSpec_260429_135339"])
def test_real_flights_accepted(name: str) -> None:
    f = check_flight(real_flight(name))
    assert f.hdr.samples == 960
    assert 60 < f.line_rate_hz < 80
    assert np.all(np.diff(f.gps.lines) > 0)
