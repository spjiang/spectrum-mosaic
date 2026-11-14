from pathlib import Path

import pytest

from ms_mosaic.local_defaults import BENCHMARK, CACHE, DATA, INPUT, PROGRAM, PYTHON, RUNS


def test_local_defaults_are_absolute():
    for p in (PROGRAM, PYTHON, INPUT, BENCHMARK, CACHE, RUNS, DATA):
        assert Path(p).is_absolute()
    assert DATA == PROGRAM.parent / "data"
    assert RUNS == DATA / "output" / "runs"
    assert INPUT == DATA / "input" / "MAX_20251017" / "MAX_20251017_001"


def test_local_input_exists_on_this_machine():
    if not INPUT.is_dir():
        pytest.skip(f"请把测区放到 {INPUT}")
    if not any(INPUT.glob("MAX_*")):
        pytest.skip(f"{INPUT} 尚未放入 MAX_* 数据（Docker 可通过 DATASET_MAX_20251017 挂载）")
