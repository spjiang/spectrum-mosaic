from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from synth import SynthFlight, write_flight  # noqa: E402

# 真实飞行数据（不进镜像、不进 CI），缺失时 realdata 用例跳过
REAL_DATA = Path(os.environ.get("PB_REAL_DATA", Path(__file__).resolve().parents[4] / "docs/需求/无人机数据"))


@pytest.fixture(autouse=True)
def _no_deployment_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主机上的 PB_SENSOR_* 不能影响用例。"""
    for name in list(os.environ):
        if name.startswith("PB_SENSOR_"):
            monkeypatch.delenv(name)


@pytest.fixture(scope="session")
def synth_flight(tmp_path_factory: pytest.TempPathFactory) -> SynthFlight:
    return write_flight(tmp_path_factory.mktemp("input") / "FigSpec_SYNTH")


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    inp, out = tmp_path / "input", tmp_path / "output"
    inp.mkdir()
    out.mkdir()
    monkeypatch.setenv("PB_INPUT_ROOTS", str(inp))
    monkeypatch.setenv("PB_OUTPUT_ROOTS", str(out))
    monkeypatch.setenv("PUSHBROOM_WORKER_STATE_DIR", str(tmp_path / "state"))
    return inp, out


def real_flight(name: str) -> Path:
    path = REAL_DATA / name
    if not (path / "figspec.hdr").is_file():
        pytest.skip(f"没有真实数据 {path}")
    return path
