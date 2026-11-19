from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from synth import BANDS, EPSG, SENSOR_PARAMS, STRIP_S, SynthFlight, write_flight

from pb_mosaic.ingest.envi import read_header
from pb_mosaic.mosaic.align import ncc_search
from pb_mosaic.mosaic.merge import Cancelled
from pb_mosaic.pipeline import JobParams, PipelineError, RunResult, run
from pb_mosaic.stages import STAGE_IDS, global_percent


@dataclass
class Recorder:
    progress_calls: list[tuple[str, float, str]] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    logs: list[tuple[str, str]] = field(default_factory=list)

    def progress(self, stage: str, fraction: float, message: str) -> None:
        self.progress_calls.append((stage, fraction, message))

    def stage_done(self, stage: str, elapsed_s: float, message: str) -> None:
        self.stages.append(stage)

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append((level, message))


def _load(out: Path, report: dict) -> np.ndarray:
    g = report["grid"]
    hdr = read_header(next((out / "products").glob("*_mosaic.hdr")))
    spe = next((out / "products").glob("*_mosaic.spe"))
    return np.fromfile(spe, dtype="<u2").reshape(g["height"], hdr.bands, g["width"])


def _truth_corr(flight: SynthFlight, cube: np.ndarray, report: dict, band: int = 0, dx: float = 0.0, dy: float = 0.0) -> float:
    g = report["grid"]
    rows, cols = np.nonzero(cube[:, band, :] > 0)
    x = g["x0"] + (cols + 0.5) * g["gsd"] + dx
    y = g["y0"] - (rows + 0.5) * g["gsd"] + dy
    return float(np.corrcoef(flight.texture(x, y), cube[rows, band, cols].astype(np.float64))[0, 1])


@pytest.fixture(scope="module")
def clean(synth_flight: SynthFlight, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, RunResult, Recorder]:
    out = tmp_path_factory.mktemp("out") / "job"
    rec = Recorder()
    return out, run(synth_flight.root, out, SENSOR_PARAMS, reporter=rec), rec


def test_mosaic_matches_ground_truth(synth_flight: SynthFlight, clean) -> None:
    out, result, _ = clean
    cube = _load(out, result.report)
    assert cube.shape[1] == BANDS
    c0 = _truth_corr(synth_flight, cube, result.report)
    assert c0 > 0.99
    # 错开 0.2 m 相关性明显下降：说明成果没有系统偏移
    for dx, dy in [(0.2, 0), (-0.2, 0), (0, 0.2), (0, -0.2)]:
        assert _truth_corr(synth_flight, cube, result.report, dx=dx, dy=dy) < c0 - 0.05
    # 各波段来自同一个原始像元：波段间比值恒定
    v = cube.transpose(0, 2, 1)[cube[:, 0, :] > 0].astype(np.float64)
    assert np.corrcoef(v[:, 0], v[:, 3])[0, 1] > 0.999


def test_report_and_products(clean) -> None:
    out, result, _ = clean
    r = result.report
    assert r["status"] == "succeeded"
    assert r["line_clock"]["method"] == "linear"
    assert len(r["strips"]) == 3
    pairs = r["alignment"]["pairs"]
    assert [p["strips"] for p in pairs] == [[0, 1], [1, 2]]
    assert all(p["used"] and p["seam"] and p["seam_m2"] > 300 for p in pairs)
    assert "2/2 条接缝参与配准" in r["alignment"]["note"]
    assert max(abs(v) for o in r["alignment"]["offsets_east_north_m"].values() for v in o) < 0.15
    kinds = [p["kind"] for p in result.products]
    assert kinds == ["cube", "header", "rgb", "preview", "footprints", "report", "report"]
    for p in result.products:
        f = out / p["path"]
        assert f.is_file() and f.stat().st_size == p["bytes"], p
    assert not list(out.rglob("*.part"))
    disk = json.loads((out / "report" / "report.json").read_text(encoding="utf-8"))
    assert disk["status"] == "succeeded" and disk["products"][0]["kind"] == "cube"
    assert "航带配准" in (out / "report" / "report.md").read_text(encoding="utf-8")
    assert (out / "process" / "trajectory.csv").read_text().startswith("record,gps_time_s")


def test_header_georeference(clean) -> None:
    out, result, _ = clean
    hdr = read_header(next((out / "products").glob("*_mosaic.hdr")))
    g = result.report["grid"]
    assert (hdr.samples, hdr.lines, hdr.bands) == (g["width"], g["height"], BANDS)
    info = hdr.fields["map info"]
    assert "UTM" in info and "49" in info and "North" in info and "WGS-84" in info
    assert hdr.fields["data ignore value"] == "0"
    assert "WGS_1984_UTM_Zone_49N" in hdr.fields["coordinate system string"]
    assert hdr.wavelengths == [460.0, 550.0, 640.0, 800.0]
    assert hdr.fields["fx model"] == "FS-60C"


def test_rgb_geotiff_and_preview(clean) -> None:
    import rasterio
    from PIL import Image

    out, result, _ = clean
    g = result.report["grid"]
    with rasterio.open(next((out / "products").glob("*_rgb.tif"))) as ds:
        assert ds.crs.to_epsg() == EPSG
        assert (ds.width, ds.height, ds.count) == (g["width"], g["height"], 3)
        assert ds.transform.a == pytest.approx(g["gsd"]) and ds.transform.c == pytest.approx(g["x0"])
        assert ds.overviews(1)
        rgb = ds.read()
    cube = _load(out, result.report)
    # default bands = {3,2,1} → RGB 依次是第 3、2、1 波段
    assert np.array_equal(rgb[0], cube[:, 2, :]) and np.array_equal(rgb[2], cube[:, 0, :])
    with Image.open(next((out / "products").glob("*_preview.png"))) as im:
        assert im.mode == "RGBA" and max(im.size) <= 2048


def test_footprints_geojson(clean) -> None:
    out, _, _ = clean
    fc = json.loads(next((out / "products").glob("*_footprints.geojson")).read_text(encoding="utf-8"))
    assert len(fc["features"]) == 3
    lon, lat = fc["features"][0]["geometry"]["coordinates"][0][0]
    assert 108 < lon < 109 and 35 < lat < 36


def test_progress_is_monotonic(clean) -> None:
    _, _, rec = clean
    assert rec.stages == list(STAGE_IDS)
    pct = [global_percent(s, f) for s, f, _ in rec.progress_calls]
    assert pct == sorted(pct)
    assert global_percent("P6_report", 1.0) == 100.0


def test_alignment_recovers_gps_bias(tmp_path: Path) -> None:
    # 中间那条航带（南飞）的定位整体偏东 0.6 m、偏南 0.4 m，成像按真实位置
    t0 = STRIP_S + 5
    flight = write_flight(tmp_path / "in" / "FigSpec_BIAS", bias=(t0 + 1, t0 + STRIP_S - 1, 0.6, -0.4))
    result = run(flight.root, tmp_path / "out", SENSOR_PARAMS)
    off = {int(k): v for k, v in result.report["alignment"]["offsets_east_north_m"].items()}
    rel = np.subtract(off[1], off[0])
    assert rel == pytest.approx([-0.6, 0.4], abs=0.15)
    assert np.subtract(off[2], off[0]) == pytest.approx([0.0, 0.0], abs=0.15)
    # 配准只定相对关系（各航带修正量之和为 0），整幅图共同平移了 off[0]；扣掉后与真值逐像元一致
    cube = _load(tmp_path / "out", result.report)
    assert _truth_corr(flight, cube, result.report, dx=-off[0][0], dy=-off[0][1]) > 0.99


def test_no_align_keeps_offsets_zero(synth_flight: SynthFlight, tmp_path: Path) -> None:
    r = run(synth_flight.root, tmp_path / "out", {**SENSOR_PARAMS, "align": False}).report
    assert all(v == [0.0, 0.0] for v in r["alignment"]["offsets_east_north_m"].values())


def test_band_subset(synth_flight: SynthFlight, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = run(synth_flight.root, out, {**SENSOR_PARAMS, "bands": [4, 2], "gsd_m": 0.2, "unknown": 1})
    hdr = read_header(next((out / "products").glob("*_mosaic.hdr")))
    assert hdr.bands == 2
    assert hdr.wavelengths == [550.0, 800.0]
    assert hdr.fields["source bands"] == "{2, 4}"
    assert result.report["grid"]["gsd"] == 0.2
    assert any("unknown" in w for w in result.report["warnings"])


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"gsd_m": 5}, "分辨率"),
        ({"bands": [0]}, "波段"),
        ({"bands": [1, 1]}, "重复"),
        ({"bands": "1-3"}, "波段"),
    ],
)
def test_bad_params(params: dict, message: str) -> None:
    with pytest.raises(PipelineError, match=message):
        JobParams.from_dict(params)


def test_band_out_of_range(synth_flight: SynthFlight, tmp_path: Path) -> None:
    with pytest.raises(PipelineError, match="超出范围"):
        run(synth_flight.root, tmp_path / "out", {**SENSOR_PARAMS, "bands": [99]})
    r = json.loads((tmp_path / "out" / "report" / "report.json").read_text(encoding="utf-8"))
    assert r["status"] == "failed" and "超出范围" in r["error"]


def test_invalid_flight_reports_failure(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(PipelineError, match="缺少文件"):
        run(tmp_path / "empty", tmp_path / "out", {})


def test_cancel_during_mosaic_cleans_up(synth_flight: SynthFlight, tmp_path: Path) -> None:
    rec = Recorder()

    def cancelled() -> bool:
        return "P3_align" in rec.stages

    out = tmp_path / "out"
    with pytest.raises(Cancelled):
        run(synth_flight.root, out, SENSOR_PARAMS, reporter=rec, cancelled=cancelled)
    assert not list((out / "products").iterdir())
    r = json.loads((out / "report" / "report.json").read_text(encoding="utf-8"))
    assert r["status"] == "cancelled"


def test_ncc_search_finds_shift() -> None:
    from scipy import ndimage

    rng = np.random.default_rng(3)
    base = ndimage.gaussian_filter(rng.standard_normal((160, 160)), 2.0)
    ok = np.ones_like(base, dtype=bool)
    tpl = np.zeros_like(base)
    tpl_ok = np.zeros_like(ok)
    tpl[40:120, 40:120] = base[47:127, 37:117]
    tpl_ok[40:120, 40:120] = True
    peaks, n = ncc_search(base, ok, tpl, tpl_ok, 15, n_peaks=2)
    assert n == 80 * 80
    assert (peaks[0].drow, peaks[0].dcol) == (7, -3)
    assert peaks[0].ncc > 0.99
