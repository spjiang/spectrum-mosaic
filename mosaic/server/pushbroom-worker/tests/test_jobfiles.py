from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from pb_mosaic.jobfiles import JobMessageError, cancel_requested, is_done, mark_done, parse_job


def _msg(inp: Path, out: Path, **kw) -> dict:
    return {
        "schema_version": 1,
        "job_id": str(uuid.uuid4()),
        "run_attempt": 1,
        "input_dir": str(inp),
        "output_dir": str(out),
        "params": {"gsd_m": 0.1},
        **kw,
    }


def test_parse_ok(roots) -> None:
    inp, out = roots
    job = parse_job(_msg(inp / "f", out / "pushbroom" / "j1"))
    assert job.run_attempt == 1 and job.params == {"gsd_m": 0.1}
    assert job.log_dir == out / "pushbroom" / "j1" / "log"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": 2}, "版本"),
        ({"job_id": "x"}, "UUID"),
        ({"run_attempt": 0}, "从 1"),
        ({"run_attempt": "a"}, "整数"),
        ({"params": []}, "params"),
        ({"input_dir": ""}, "缺少"),
    ],
)
def test_parse_rejects(roots, change: dict, message: str) -> None:
    inp, out = roots
    with pytest.raises(JobMessageError, match=message):
        parse_job({**_msg(inp / "f", out / "j"), **change})


def test_parse_rejects_paths_outside_roots(roots, tmp_path: Path) -> None:
    inp, out = roots
    with pytest.raises(JobMessageError, match="输入目录"):
        parse_job(_msg(tmp_path / "elsewhere", out / "j"))
    with pytest.raises(JobMessageError, match="输入目录"):
        parse_job(_msg(inp / ".." / "x", out / "j"))
    with pytest.raises(JobMessageError, match="输出目录"):
        parse_job(_msg(inp / "f", out))
    with pytest.raises(JobMessageError, match="输出目录"):
        parse_job(_msg(inp / "f", tmp_path / "x"))
    with pytest.raises(JobMessageError, match="JSON"):
        parse_job([1])


def test_cancel_file_matches_attempt(roots) -> None:
    inp, out = roots
    job = parse_job(_msg(inp / "f", out / "j", run_attempt=2))
    assert not cancel_requested(job)
    job.log_dir.mkdir(parents=True)
    ctl = job.log_dir / "control.json"
    ctl.write_text(json.dumps({"cancel": True, "run_attempt": 1}))
    assert not cancel_requested(job)
    ctl.write_text(json.dumps({"cancel": True, "run_attempt": 2}))
    assert cancel_requested(job)
    ctl.write_text("{broken")
    assert not cancel_requested(job)


def test_done_marker_per_attempt(roots) -> None:
    inp, out = roots
    m = _msg(inp / "f", out / "j")
    job = parse_job(m)
    assert not is_done(job)
    mark_done(job, "succeeded", "ok")
    assert is_done(job)
    assert not is_done(parse_job({**m, "run_attempt": 2}))
