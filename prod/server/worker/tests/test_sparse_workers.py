from pathlib import Path
from types import SimpleNamespace

from ms_mosaic.runner import cap_at_workers


def test_cap_at_workers_by_available_memory(monkeypatch):
    monkeypatch.setattr("ms_mosaic.runner._cpu_count", lambda: 16)
    monkeypatch.setattr("ms_mosaic.runner._available_memory_bytes", lambda: 2 * 1024**3)
    n = cap_at_workers(12, per_worker_mb=450, reserve_mb=1024)
    assert n == 2


def test_cap_at_workers_falls_back_to_one_when_tight(monkeypatch):
    monkeypatch.setattr("ms_mosaic.runner._cpu_count", lambda: 16)
    monkeypatch.setattr("ms_mosaic.runner._available_memory_bytes", lambda: 400 * 1024**2)
    assert cap_at_workers(12) == 1


def test_cap_at_workers_honors_request_when_ram_plenty(monkeypatch):
    monkeypatch.setattr("ms_mosaic.runner._cpu_count", lambda: 16)
    monkeypatch.setattr("ms_mosaic.runner._available_memory_bytes", lambda: 64 * 1024**3)
    assert cap_at_workers(3) == 3


def test_cap_at_workers_uses_job_budget_not_host_available(monkeypatch):
    monkeypatch.setattr("ms_mosaic.runner._cpu_count", lambda: 16)
    monkeypatch.setattr("ms_mosaic.runner._available_memory_bytes", lambda: 400 * 1024**2)
    n = cap_at_workers(12, budget_bytes=36 * 1024**3)
    assert n == 12


def test_cap_at_workers_with_budget_does_not_hardcap_eight(monkeypatch):
    monkeypatch.setattr("ms_mosaic.runner._cpu_count", lambda: 16)
    monkeypatch.setattr("ms_mosaic.runner._available_memory_bytes", lambda: 400 * 1024**2)
    assert cap_at_workers(12, budget_bytes=32 * 1024**3) == 12


def test_match_init_does_not_preload_all_features(tmp_path: Path, monkeypatch):
    from ms_mosaic import runner

    calls = []

    def fake_extract(path, cache_dir=None, max_features=8192):
        calls.append(Path(path).name)
        return SimpleNamespace()

    monkeypatch.setattr(runner, "extract_file", fake_extract)
    runner._SHARED.clear()
    runner._match_init(str(tmp_path), SimpleNamespace(fx=1))
    assert calls == []
    assert "feats" not in runner._SHARED
    assert runner._SHARED["cache_dir"] == str(tmp_path)
