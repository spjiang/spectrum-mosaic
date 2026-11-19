"""精配准（逐条剪切 + 平移细化）与传感器标定自检。合成架次的真值已知。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from synth import SENSOR_PARAMS, SynthFlight, write_flight
from test_pipeline import _load, _truth_corr

from pb_mosaic.__main__ import main as cli
from pb_mosaic.geom.calibration import CalibrationCheck, combine
from pb_mosaic.pipeline import JobParams, PipelineError, run

SHEAR = (3.0, -3.0)


@pytest.fixture(scope="module")
def sheared(tmp_path_factory: pytest.TempPathFactory) -> SynthFlight:
    return write_flight(tmp_path_factory.mktemp("in") / "FigSpec_SHEAR", shear_deg=SHEAR)


@pytest.fixture(scope="module")
def sheared_runs(sheared: SynthFlight, tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = {}
    for name, extra in (("rigid", {"refine": False}), ("refined", {})):
        d = tmp_path_factory.mktemp(name) / "job"
        out[name] = (d, run(sheared.root, d, {**SENSOR_PARAMS, **extra}).report)
    return out


def test_refine_recovers_shear(sheared_runs: dict) -> None:
    _, r = sheared_runs["refined"]
    ref = r["alignment"]["refine"]
    assert ref["applied"], ref["note"]
    shear = {s["index"]: s["shear_deg"] for s in r["strips"]}
    # 北、南、北；往返平均互为相反数的约定与真值一致
    assert [shear[0], shear[1], shear[2]] == pytest.approx([3.0, -3.0, 3.0], abs=0.4)
    assert ref["median_after_m"] < 0.5 * ref["median_before_m"]
    assert ref["median_after_m"] < 0.05
    assert all(p["tiles_after"] >= 4 for p in ref["pairs"])
    md = (sheared_runs["refined"][0] / "report" / "report.md").read_text(encoding="utf-8")
    assert "### 精配准" in md and "狭缝剪切" in md


def test_refine_improves_mosaic_against_truth(sheared: SynthFlight, sheared_runs: dict) -> None:
    corr = {}
    for name, (d, r) in sheared_runs.items():
        off = {int(k): v for k, v in r["alignment"]["offsets_east_north_m"].items()}
        corr[name] = _truth_corr(sheared, _load(d, r), r, dx=-off[0][0], dy=-off[0][1])
    assert sheared_runs["rigid"][1]["alignment"]["refine"]["applied"] is False
    assert corr["refined"] > 0.99
    assert corr["refined"] > corr["rigid"] + 0.02


def test_refine_on_clean_flight_is_quiet(synth_flight: SynthFlight, tmp_path: Path) -> None:
    r = run(synth_flight.root, tmp_path / "out", SENSOR_PARAMS).report
    ref = r["alignment"]["refine"]
    assert not any("精配准" in w for w in r["warnings"])
    assert max(abs(s["shear_deg"]) for s in r["strips"]) < 0.3
    assert ref["median_after_m"] < 0.05


def test_job_params_flags() -> None:
    assert JobParams.from_dict({}).refine is True
    assert JobParams.from_dict({"refine": False}).as_dict()["refine"] is False
    with pytest.raises(PipelineError, match="refine"):
        JobParams.from_dict({"refine": "no"})


def test_calibration_detects_wrong_mounting(tmp_path: Path) -> None:
    # 真实安装：沿航向 0.8 m、狭缝中心偏 +8 像元；处理时按 0 / 0 算
    flight = write_flight(tmp_path / "in" / "FigSpec_MOUNT", sensor={"along_offset_m": 0.8, "boresight_px": 8.0})
    r = run(flight.root, tmp_path / "out", SENSOR_PARAMS).report
    cal = r["calibration"]
    assert cal["usable"]
    assert cal["along_suggested_m"] == pytest.approx(0.8, abs=0.1)
    assert cal["boresight_suggested_px"] == pytest.approx(8.0, abs=1.0)
    assert cal["warning"] and "calibrate" in cal["warning"]
    assert any("传感器标定与本架次不符" in w for w in r["warnings"])
    md = (tmp_path / "out" / "report" / "report.md").read_text(encoding="utf-8")
    assert "## 传感器标定自检" in md


def test_calibration_quiet_when_mounting_right(synth_flight: SynthFlight, tmp_path: Path) -> None:
    cal = run(synth_flight.root, tmp_path / "out", SENSOR_PARAMS).report["calibration"]
    assert cal["usable"] and cal["warning"] is None
    assert abs(cal["along_suggested_m"]) < 0.1 and abs(cal["boresight_suggested_px"]) < 1.0


def _check(along: float, bore: float, se_a: float = 0.1, se_b: float = 3.0) -> CalibrationCheck:
    return CalibrationCheck(
        usable=True,
        strips=5,
        note="",
        gsd_cross_m=0.0375,
        along_suggested_m=along,
        along_se_m=se_a,
        boresight_suggested_px=bore,
        boresight_se_px=se_b,
    )


def test_combine_weights_and_rejects_inconsistent() -> None:
    ok = combine([_check(1.65, -19.0), _check(1.75, -20.0)], ["a", "b"])
    assert ok["usable"] and ok["along_offset_m"] == pytest.approx(1.7) and ok["boresight_px"] == pytest.approx(-19.5)
    bad = combine([_check(1.8, -50.0), _check(1.65, -19.0), _check(1.75, -20.0)], ["0414", "0419", "0429"])
    assert not bad["usable"] and "狭缝中心" in bad["note"] and "0414" in bad["note"]
    assert not combine([CalibrationCheck(usable=False, strips=1, note="")])["usable"]


@pytest.mark.realdata
def test_real_flight_refine_and_calibration() -> None:
    from conftest import real_flight

    from pb_mosaic.pipeline import calibrate_flight

    r = calibrate_flight(real_flight("FigSpec_260419_132008"))
    cal = r["calibration"]
    assert r["refine"]["applied"] and r["refine"]["median_after_m"] < 0.25
    assert r["refine"]["median_after_m"] < 0.5 * r["refine"]["median_before_m"]
    assert cal.usable and cal.warning is None
    assert cal.along_suggested_m == pytest.approx(1.7, abs=0.4)
    assert cal.boresight_suggested_px == pytest.approx(-19.0, abs=10.0)


def test_cli_calibrate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    flight = write_flight(tmp_path / "FigSpec_CLI", sensor={"along_offset_m": 0.5})
    params = json.dumps({k: v for k, v in SENSOR_PARAMS.items()})
    assert cli(["calibrate", "--input", str(flight.root), "--params", params]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["flights"][0]["flight"] == "FigSpec_CLI"
    assert out["combined"]["along_offset_m"] == pytest.approx(0.5, abs=0.1)
    assert out["env"][0].startswith("PB_SENSOR_ALONG_OFFSET_M=")
    assert cli(["calibrate", "--input", str(tmp_path / "missing")]) == 1
