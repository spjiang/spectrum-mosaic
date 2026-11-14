from datetime import datetime
from pathlib import Path

from ms_mosaic.run_paths import stamp_run_output_dir


def test_stamp_run_output_dir_appends_datetime():
    out = stamp_run_output_dir(
        "/data/output/runs/demo_max_20251017_rgb",
        when=datetime(2026, 9, 20, 20, 53, 12),
    )
    assert out == Path("/data/output/runs/demo_max_20251017_rgb/20260920_205312")


def test_stamp_run_output_dir_skips_if_already_stamped():
    p = "/data/output/runs/demo_max_20251017_full/20260919_202140"
    assert stamp_run_output_dir(p) == Path(p)
