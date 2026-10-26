"""正射 GPU 只在进程少时绑定，避免 32 个 CUDA 上下文把 T4 排队拖慢。"""

from ms_mosaic.compose import GPU_ORTHO_MAX_PROCS, _ortho_gpu_fields
from ms_mosaic.gpu import GpuChoice


def test_skip_gpu_when_too_many_tile_procs(monkeypatch):
    from ms_mosaic import compose

    monkeypatch.setattr(compose, "resolve_workers", lambda *_a, **_k: 32)
    choice = GpuChoice(device=0, budget_bytes=8 * 2**30, name="Tesla T4", reason="")
    logs: list[str] = []
    assert _ortho_gpu_fields(choice, 32, None, logs.append, "正射 Color") == {}
    assert "CPU" in logs[0]
    assert str(GPU_ORTHO_MAX_PROCS) in logs[0]


def test_attach_gpu_when_few_tile_procs(monkeypatch):
    from ms_mosaic import compose

    monkeypatch.setattr(compose, "resolve_workers", lambda *_a, **_k: 4)
    choice = GpuChoice(device=0, budget_bytes=8 * 2**30, name="Tesla T4", reason="")
    fields = _ortho_gpu_fields(choice, 4, None, None, "正射 Color")
    assert fields["gpu_device"] == 0
    assert fields["gpu_budget_per_proc"] == 2 * 2**30


def test_cpu_choice_never_attaches_gpu():
    choice = GpuChoice(device=None, budget_bytes=0, name="", reason="任务设置为不用 GPU")
    logs: list[str] = []
    assert _ortho_gpu_fields(choice, 4, None, logs.append, "正射 Color") == {}
    assert "不用 GPU" in logs[0]