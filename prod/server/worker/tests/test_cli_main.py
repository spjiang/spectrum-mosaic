"""CLI 旗标与 Web 方案 param key 对齐。"""

from __future__ import annotations

import pytest

from ms_mosaic.__main__ import args_to_params, build_parser


def test_cli_flags_match_web_keys():
    p = build_parser()
    dests = {a.dest for a in p._actions if a.dest not in ("help",)}
    # 核心键必须存在
    for key in (
        "input_dir",
        "output_dir",
        "cache_dir",
        "dsm_gsd",
        "workers_at",
        "workers_dense",
        "workers_ortho",
        "drop_white_panel",
        "radiometric_normalize",
        "edge_trim_m",
    ):
        assert key in dests, key


def test_old_flags_rejected():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["--input", "/tmp/in", "--out", "/tmp/out"])


def test_args_to_params_maps_names(tmp_path):
    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    p = build_parser()
    args = p.parse_args(
        [
            "--input-dir",
            str(inp),
            "--output-dir",
            str(out),
            "--dsm-gsd",
            "0.1",
            "--workers-at",
            "4",
            "--no-drop-white-panel",
            "--bands",
            "Color,550nm",
        ]
    )
    params = args_to_params(args)
    assert params["input_dir"] == str(inp)
    assert params["output_dir"] == str(out)
    assert params["dsm_gsd"] == 0.1
    assert params["workers_at"] == 4
    assert params["drop_white_panel"] is False
    assert params["bands"] == ["Color", "550nm"]


def test_preset_rgb_preview_forces_color(tmp_path):
    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    p = build_parser()
    args = p.parse_args(
        ["--input-dir", str(inp), "--output-dir", str(out), "--preset", "rgb_preview", "--bands", "Color,850nm"]
    )
    params = args_to_params(args)
    assert params["bands"] == ["Color"]
