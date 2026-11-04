"""野外检测点验收：先在正射上选点，再填野外坐标，评估后计算像素误差。"""

from types import SimpleNamespace

import numpy as np
import pytest
import tifffile
from fastapi import HTTPException

from app.services.check_accuracy import (
    add_point,
    build_state,
    map_from_pixel,
    parse_csv,
    pixel_from_map,
    run_evaluation,
    set_gps,
    update_config,
    window_png,
)


def _job(tmp_path):
    products = tmp_path / "拼图结果"
    products.mkdir()
    citation = "WGS 84 / UTM zone 50N|"
    # 原点 (200000, 2500000)，GSD 1 m。像元 (1,1) 的地面坐标是 (200001, 2499999)。
    tifffile.imwrite(
        products / "Orthomosaic_pix_surf_group0.tif",
        np.arange(16, dtype=np.uint8).reshape(4, 4),
        photometric="minisblack",
        extratags=[
            (33550, 12, 3, (1.0, 1.0, 0.0), False),
            (33922, 12, 6, (0.0, 0.0, 0.0, 200000.0, 2500000.0, 0.0), False),
            (34737, 2, len(citation), citation, False),
        ],
    )
    return SimpleNamespace(output_dir=str(tmp_path), products_dir=str(products), log_dir=None)


def test_parse_geographic_csv():
    raw = "点名,经度,纬度,高程,坐标系\nCP01,114.1,22.2,3.5,EPSG:4326\n".encode()
    crs, points = parse_csv(raw)
    assert crs == "EPSG:4326"
    assert points == [{"name": "CP01", "x": 114.1, "y": 22.2, "z": 3.5}]


def test_pixel_roundtrip():
    east, north = map_from_pixel(1, 1, 200000, 2500000, 1)
    col, row = pixel_from_map(east, north, 200000, 2500000, 1)
    assert east == pytest.approx(200001)
    assert north == pytest.approx(2499999)
    assert col == pytest.approx(1)
    assert row == pytest.approx(1)


def test_pick_then_gps_then_evaluate(tmp_path):
    job = _job(tmp_path)
    picked = add_point(job, 1, 1)
    assert picked["evaluated"] is False
    assert picked["accepted"] is False
    assert "评估校验" in picked["message"]
    point = picked["points"][0]
    assert point["name"] == "CP01"
    assert point["east"] == pytest.approx(200001)
    assert point["north"] == pytest.approx(2499999)
    assert point["err_px"] is None
    update_config(job, 1, 1, 2)
    filled = set_gps(job, "CP01", 200001, 2499999, 0, "EPSG:32650")
    assert filled["evaluated"] is False
    passed = run_evaluation(job)
    assert passed["accepted"] is True
    assert passed["points"][0]["err_px"] == pytest.approx(0)
    png, meta = window_png(job, "group0", None, None)
    assert png.startswith(b"\x89PNG")
    assert meta["full_width"] == 4
    # 恰好 2 像素不算通过
    set_gps(job, "CP01", 200003, 2499999, 0, "EPSG:32650")
    edge = run_evaluation(job)
    assert edge["points"][0]["err_px"] == pytest.approx(2)
    assert edge["accepted"] is False
    assert edge["clauses"][2]["result"] == "不满足"
    assert "REQ-05-01" not in edge["message"]


def test_count_outside_configured_range_fails(tmp_path):
    job = _job(tmp_path)
    add_point(job, 1, 1)
    set_gps(job, "CP01", 200001, 2499999, 0, "EPSG:32650")
    update_config(job, 20, 30, 2)
    state = run_evaluation(job)
    assert state["accepted"] is False
    assert state["clauses"][0]["result"] == "不满足"


def test_old_imported_points_are_not_picks(tmp_path):
    job = _job(tmp_path)
    folder = tmp_path / "附件"
    folder.mkdir()
    (folder / "accuracy_check.json").write_text(
        '{"points":[{"name":"CP01","x":1,"y":2,"z":0}],"marks":{"group0":{"CP01":{"col":1,"row":1}}}}',
        encoding="utf-8",
    )
    state = build_state(job)
    assert state["points"] == []
    assert "选择检测点" in state["message"]


def test_duplicate_name_rejected():
    raw = "点名,经度,纬度,高程,坐标系\nA,1,2,0,EPSG:4326\nA,1,2,0,EPSG:4326\n".encode()
    with pytest.raises(HTTPException):
        parse_csv(raw)
