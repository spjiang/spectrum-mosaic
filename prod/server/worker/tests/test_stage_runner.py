from pathlib import Path
from unittest.mock import MagicMock

from ms_mosaic.stage_runner import run_stages


def test_run_stages_forwards_template_params(tmp_path: Path, monkeypatch):
    captured: dict = {}

    def fake_run_mosaic(inp, out, **kwargs):
        captured.update(kwargs)
        return {"status": "succeeded", "n_shots": 3}

    monkeypatch.setattr("ms_mosaic.stage_runner.run_mosaic", fake_run_mosaic)
    monkeypatch.setattr("ms_mosaic.stage_runner.set_job_cpus", lambda *_a, **_k: None)
    monkeypatch.setattr("ms_mosaic.stage_runner.resolve_cpu_budget", lambda *_a, **_k: 4)

    inp = tmp_path / "in"
    inp.mkdir()
    out = tmp_path / "out"
    result = run_stages(
        inp,
        out,
        params={
            "input_dir": str(inp),
            "output_dir": str(out),
            "workers_at": 4,
            "workers_dense": 6,
            "workers_ortho": 8,
            "min_agl_m": 7.5,
            "outlier_threshold_px": 8,
            "n_layers": 32,
            "color_correction": "off",
            "write_pdf_report": False,
            "seamline_enabled": True,
            "edge_trim_m": 20,
            "flatten_edge_win_m": 40,
            "flatten_edge_band_m": 80,
            "run_mode": "full",
        },
    )
    assert result["status"] == "succeeded"
    assert captured["workers_at"] == 4
    assert captured["workers_dense"] == 6
    assert captured["workers_ortho"] == 8
    assert captured["min_agl_m"] == 7.5
    assert captured["outlier_threshold_px"] == 8.0
    assert captured["n_layers"] == 32
    assert captured["color_correction"] == "off"
    assert captured["write_pdf_report"] is False
    assert captured["seamline_enabled"] is True
    assert captured["edge_trim_m"] == 20.0
    assert captured["flatten_edge_win_m"] == 40.0
    assert captured["flatten_edge_band_m"] == 80.0
