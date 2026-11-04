"""成果下载只允许拼图目录里的原文件，质量报告只给出汇总。"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.job_files import build_archive, catalog, original_file, quality_view


def _job(tmp_path):
    products = tmp_path / "拼图结果"
    products.mkdir()
    (products / "Orthomosaic_pix_surf_group0.tif").write_bytes(b"tif")
    (products / "DSM.tif").write_bytes(b"dsm")
    (products / "rgb_preview.png").write_bytes(b"png")
    (tmp_path / "质量报告.pdf").write_bytes(b"pdf")
    report = tmp_path / "report"
    report.mkdir()
    (report / "quality.json").write_text(
        json.dumps(
            {
                "project_name": "demo",
                "n_images": 2,
                "n_registered": 2,
                "area_km2": 0.35,
                "at": {"rms_reprojection_px": 0.5, "gps_rmse_m": 1.2},
                "dsm": {"gsd": 0.1, "z_median": 30},
                "ortho": {"gsd": 0.05, "mode": "基于DSM逐像素拼接"},
                "cameras": {"Color": {"group": 0, "n_images": 2, "n_registered": 2, "rms_px": 0.4}},
                "input": "/secret/path",
                "gps_rows": [{"name": "should-not-leak"}],
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(output_dir=str(tmp_path), products_dir=str(products), log_dir=None, seq=9, report_pdf_path=str(tmp_path / "质量报告.pdf"))


def test_catalog_lists_tifs_and_hides_preview(tmp_path):
    data = catalog(_job(tmp_path))
    names = [item["name"] for item in data["originals"]]
    assert "Orthomosaic_pix_surf_group0.tif" in names
    assert "DSM.tif" in names
    assert "rgb_preview.png" not in names
    assert data["bands"][0]["label"]


def test_original_file_rejects_escape(tmp_path):
    job = _job(tmp_path)
    assert original_file(job, "DSM.tif").name == "DSM.tif"
    with pytest.raises(HTTPException):
        original_file(job, "../质量报告.pdf")
    with pytest.raises(HTTPException):
        original_file(job, "rgb_preview.png")


def test_quality_view_drops_raw_rows(tmp_path):
    view = quality_view(_job(tmp_path))
    assert view["ready"] is True
    assert view["at"]["rms_reprojection_px"] == 0.5
    assert "gps_rows" not in view
    assert view["input"] if False else "input" not in view
    assert view["cameras"][0]["band"] == "Color"


def test_archive_original_is_only_tifs(tmp_path):
    import zipfile

    job = _job(tmp_path)
    path, filename = build_archive(job, "original")
    try:
        assert filename == "任务9_拼图原文件.zip"
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        assert "拼图结果/DSM.tif" in names
        assert "拼图结果/rgb_preview.png" not in names
        assert "质量报告.pdf" not in names
    finally:
        path.unlink()
