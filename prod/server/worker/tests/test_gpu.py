import sys
import types

import pytest

from ms_mosaic import gpu
from ms_mosaic.gpu import GpuInfo, choose, describe, parse_gpu_setting

T4 = GpuInfo(index=0, name="Tesla T4", total_mb=15360, free_mb=15000, capability=(7, 5))
T4_BUSY = GpuInfo(index=1, name="Tesla T4", total_mb=15360, free_mb=4000, capability=(7, 5))
OLD = GpuInfo(index=0, name="Tesla K80", total_mb=12000, free_mb=12000, capability=(3, 7))
ALL = frozenset({"S5_ortho"})


@pytest.mark.parametrize(
    "raw,expected",
    [(None, "auto"), ("", "auto"), ("AUTO", "auto"), ("off", "off"), ("none", "off"), ("1", "1"), (0, "0"), ("x", "auto"), ("-1", "auto")],
)
def test_parse_gpu_setting(raw, expected):
    assert parse_gpu_setting(raw) == expected


def test_off_uses_cpu():
    c = choose("off", None, [T4], None, stage="S5_ortho", validated=ALL)
    assert c.device is None
    assert "不用 GPU" in c.reason


def test_no_gpu_reports_reason():
    c = choose("auto", None, [], "未安装 GPU 运行库", stage="S5_ortho", validated=ALL)
    assert c.device is None
    assert "未安装 GPU 运行库" in c.reason


def test_low_capability_card_is_skipped():
    c = choose("auto", None, [OLD], None, stage="S5_ortho", validated=ALL)
    assert c.device is None
    assert "计算能力" in c.reason


def test_auto_picks_card_with_most_free_memory():
    c = choose("auto", None, [T4_BUSY, T4], None, stage="S5_ortho", validated=ALL)
    assert c.device == 0
    assert c.budget_bytes == int(15000 * 2**20 * gpu.AUTO_FRACTION)


def test_explicit_card_missing_falls_back():
    c = choose("3", None, [T4], None, stage="S5_ortho", validated=ALL)
    assert c.device is None
    assert "3" in c.reason


def test_explicit_card_selected():
    c = choose("1", None, [T4, T4_BUSY], None, stage="S5_ortho", validated=ALL)
    assert c.device == 1


def test_memory_cap_never_exceeds_free():
    small = choose("0", 2.0, [T4], None, stage="S5_ortho", validated=ALL)
    assert small.budget_bytes == 2 * 2**30
    huge = choose("0", 64.0, [T4], None, stage="S5_ortho", validated=ALL)
    assert huge.budget_bytes == 15000 * 2**20


def test_unvalidated_stage_stays_on_cpu():
    c = choose("auto", None, [T4], None, stage="S3_dense", validated=ALL)
    assert c.device is None
    assert "验收" in c.reason


def test_describe_lines():
    on = choose("auto", 13.0, [T4], None, stage="S5_ortho", validated=ALL)
    assert describe("正射", on) == "正射：GPU 0 Tesla T4，显存上限 13.0 GB"
    off = choose("auto", None, [], "未安装 GPU 运行库", stage="S5_ortho", validated=ALL)
    assert describe("正射", off) == "正射：未安装 GPU 运行库，使用 CPU"


def test_probe_without_cupy(monkeypatch):
    monkeypatch.setitem(sys.modules, "cupy", None)
    gpus, reason = gpu.probe()
    assert gpus == []
    assert reason == "未安装 GPU 运行库"


def _fake_cupy(cards):
    runtime = types.SimpleNamespace(
        getDeviceCount=lambda: len(cards),
        getDeviceProperties=lambda i: {
            "name": cards[i][0].encode(),
            "major": cards[i][1][0],
            "minor": cards[i][1][1],
            "totalGlobalMem": cards[i][2] * 2**20,
        },
    )

    class Device:
        def __init__(self, i):
            self.i = i

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def mem_info(self):
            return (cards[self.i][3] * 2**20, cards[self.i][2] * 2**20)

    return types.SimpleNamespace(cuda=types.SimpleNamespace(runtime=runtime, Device=Device))


def test_probe_reads_cards(monkeypatch):
    monkeypatch.setitem(sys.modules, "cupy", _fake_cupy([("Tesla T4", (7, 5), 15360, 15000)]))
    gpus, reason = gpu.probe()
    assert reason is None
    assert gpus == [T4]


def test_probe_zero_devices(monkeypatch):
    monkeypatch.setitem(sys.modules, "cupy", _fake_cupy([]))
    gpus, reason = gpu.probe()
    assert gpus == []
    assert reason == "未检测到显卡"


def test_select_uses_job_setting(monkeypatch):
    monkeypatch.setattr(gpu, "probe", lambda: ([T4], None))
    monkeypatch.setattr(gpu, "VALIDATED_STAGES", ALL)
    gpu.set_job_gpu("off", None)
    try:
        assert gpu.select("S5_ortho").device is None
        gpu.set_job_gpu("auto", 4.0)
        chosen = gpu.select("S5_ortho")
        assert chosen.device == 0
        assert chosen.budget_bytes == 4 * 2**30
    finally:
        gpu.set_job_gpu(None, None)


def test_parse_nvidia_smi():
    text = "0, Tesla T4, 15360, 15000, 7.5\n1, Tesla T4, 15360, 4000, 7.5\nbad line\n"
    assert gpu.parse_nvidia_smi(text) == [T4, T4_BUSY]


def test_summary_caches_and_reports_missing_runtime(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "_summary_cache", None)
    monkeypatch.setattr(gpu, "_query_nvidia_smi", lambda: (calls.append(1), ([T4], None))[1])
    monkeypatch.setattr(gpu, "_runtime_installed", lambda: False)
    first = gpu.summary(now=100.0)
    assert first["available"] is False
    assert first["reason"] == "未安装 GPU 运行库"
    assert first["devices"][0]["capability"] == "7.5"
    gpu.summary(now=130.0)
    assert len(calls) == 1
    gpu.summary(now=100.0 + gpu.SUMMARY_TTL_S + 1)
    assert len(calls) == 2


def test_trial_stages_env_opens_unvalidated_stage(monkeypatch):
    monkeypatch.setattr(gpu, "probe", lambda: ([T4], None))
    monkeypatch.setattr(gpu, "VALIDATED_STAGES", frozenset())
    gpu.set_job_gpu("auto", None)
    try:
        monkeypatch.delenv("MOSAIC_GPU_TRIAL_STAGES", raising=False)
        assert gpu.select("S5_ortho").device is None
        monkeypatch.setenv("MOSAIC_GPU_TRIAL_STAGES", "S5_ortho, S3_dense")
        chosen = gpu.select("S5_ortho")
        assert chosen.device == 0
        assert "验收试运行" in describe("正射", chosen)
        gpu.set_job_gpu("off", None)
        assert gpu.select("S5_ortho").device is None
    finally:
        gpu.set_job_gpu(None, None)
