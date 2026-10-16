from pathlib import Path

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services.paths import validate_paths

STAGES = ["S0_io", "S1_catalog", "S2_at", "S3_dense", "S4_dsm", "S5_ortho", "S6_report"]


def test_stages_cover_pipeline():
    assert "S5_ortho" in STAGES
    assert STAGES[0] == "S0_io"


def test_validate_paths_rejects_nested_out(tmp_path: Path):
    settings = Settings(data_roots=str(tmp_path))
    inp = tmp_path / "in"
    out = inp / "out"
    inp.mkdir()
    out.mkdir()
    with pytest.raises(HTTPException):
        validate_paths(settings, str(inp), str(out))


def test_stamp_run_output_dir_appends_datetime():
    from datetime import datetime

    from app.services.paths import stamp_run_output_dir

    out = stamp_run_output_dir(
        "/data/output/runs/demo_max_20251017_full",
        when=datetime(2026, 9, 19, 20, 19, 7),
    )
    assert out == "/data/output/runs/demo_max_20251017_full/20260919_201907"


def test_stamp_run_output_dir_skips_if_already_stamped():
    from app.services.paths import stamp_run_output_dir

    p = "/data/output/runs/demo/20260919_201500"
    assert stamp_run_output_dir(p) == p


def test_resolve_paths_puts_report_at_run_root(tmp_path: Path):
    from app.services.jobs import resolve_paths

    out = tmp_path / "out"
    paths = resolve_paths({}, str(tmp_path / "in"), str(out))
    assert paths["report_pdf_path"] == str(out.resolve() / "质量报告.pdf")
    assert paths["products_dir"] == str(out.resolve() / "拼图结果")


def test_locate_report_pdf_falls_back_to_run_root(tmp_path: Path):
    from types import SimpleNamespace

    from app.services.jobs import locate_report_pdf

    pdf = tmp_path / "质量报告.pdf"
    pdf.write_bytes(b"%PDF")
    job = SimpleNamespace(
        report_pdf_path=str(tmp_path / "拼图结果" / "质量报告.pdf"),
        output_dir=str(tmp_path),
        products_dir=str(tmp_path / "拼图结果"),
    )
    assert locate_report_pdf(job) == pdf


def test_detach_derived_dirs_clears_paths_under_old_output():
    from app.services.paths import detach_derived_dirs

    snap = {
        "cache_dir": "/data/output/runs/demo/cache/features",
        "log_dir": "/tmp/keep-me",
        "process_dir": "/data/output/runs/demo/附件",
    }
    detach_derived_dirs(snap, "/data/output/runs/demo")
    assert snap["cache_dir"] is None
    assert snap["log_dir"] == "/tmp/keep-me"
    assert snap["process_dir"] is None
