from pathlib import Path
from types import SimpleNamespace

import numpy as np

from ms_mosaic.features import Features
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


def test_match_worker_reads_cache_at_the_job_limit(tmp_path: Path, monkeypatch):
    """匹配进程必须带上任务的特征上限，否则会按默认 8192 重提并改写缓存。"""
    from ms_mosaic import runner

    seen = []

    def fake_extract(path, cache_dir=None, max_features=8192, device=None):
        seen.append(max_features)
        return Features(np.zeros((1, 4), np.float32), np.zeros((1, 128), np.float32))

    monkeypatch.setattr(runner, "extract_file", fake_extract)
    monkeypatch.setattr(runner, "match_pair", lambda *_a, **_k: None)
    runner._SHARED.clear()
    runner._match_init(str(tmp_path), {"Color": object()}, None, 512)
    runner._match_worker((0, 1, str(tmp_path / "a.tif"), str(tmp_path / "b.tif"), "Color"))
    assert seen == [512, 512]


def test_group_match_jobs_keeps_each_pair_and_reuses_the_hub():
    from ms_mosaic.runner import group_match_jobs

    jobs = [
        (0, 1, "p0", "p1", "Color"),
        (0, 2, "p0", "p2", "Color"),
        (0, 3, "p0", "p3", "Color"),
        (8, 9, "p8", "p9", "Color"),
    ]
    groups = group_match_jobs(jobs)
    flat = [job for group in groups for _orig, job in group]
    assert sorted(flat) == sorted(jobs)
    origs = [orig for group in groups for orig, _job in group]
    assert sorted(origs) == list(range(len(jobs)))
    hub = [group for group in groups if any(job[0] == 0 for _orig, job in group)]
    assert len(hub) == 1
    assert len(hub[0]) == 3


def test_match_group_reads_each_image_once(tmp_path: Path, monkeypatch):
    from ms_mosaic import runner

    calls = []

    def fake_extract(path, cache_dir=None, max_features=8192, device=None):
        calls.append(Path(path).name)
        desc = np.zeros((8, 128), np.float32)
        desc[:, 0] = np.arange(8, dtype=np.float32) + len(calls)
        return Features(np.zeros((8, 4), np.float32), desc)

    monkeypatch.setattr(runner, "extract_file", fake_extract)
    monkeypatch.setattr(runner, "match_pair", lambda *_a, **_k: None)
    runner._SHARED.clear()
    runner._match_init(str(tmp_path), {"Color": object()}, None, 64)
    runner._match_group(
        [
            (0, (0, 1, str(tmp_path / "a.tif"), str(tmp_path / "b.tif"), "Color")),
            (1, (0, 2, str(tmp_path / "a.tif"), str(tmp_path / "c.tif"), "Color")),
        ]
    )
    assert calls == ["a.tif", "b.tif", "c.tif"]


def test_match_all_forwards_feature_limit(monkeypatch, tmp_path: Path):
    from ms_mosaic import runner

    seen = {}

    def fake(*_a, max_features=8192, **_k):
        seen["max_features"] = max_features
        return []

    monkeypatch.setattr(runner, "_match_all_cpu", fake)
    runner._match_all([], 4, tmp_path, {}, max_features=512, log=lambda *_: None)
    assert seen["max_features"] == 512


class _InlinePool:
    def __init__(self, workers):
        self.workers = workers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, arg):
        from concurrent.futures import Future

        fut = Future()
        try:
            fut.set_result(fn(arg))
        except Exception as exc:
            fut.set_exception(exc)
        return fut


def test_overlap_matches_only_after_features_exist(monkeypatch):
    from ms_mosaic import runner

    order = []

    def fake_extract(job):
        order.append(("extract", int(job[0])))
        return int(job[0])

    def fake_group(indexed):
        ids = []
        out = []
        for orig, job in indexed:
            ids.extend((int(job[0]), int(job[1])))
            out.append((orig, (int(job[0]), int(job[1]), np.zeros((1, 2)), "essential")))
        order.append(("match", tuple(ids)))
        return out

    monkeypatch.setattr(runner, "_extract_worker", fake_extract)
    monkeypatch.setattr(runner, "_match_group", fake_group)
    jobs = [
        (0, "/in/a.tif", "/cache", 32),
        (1, "/in/b.tif", "/cache", 32),
    ]
    pairs = [(0, 1, "/in/a.tif", "/in/b.tif", "Color")]
    matches, _feat_s = runner._overlap_extract_match(
        jobs,
        pairs,
        2,
        Path("/cache"),
        {},
        max_features=32,
        log=lambda *_a: None,
        pool_factory=_InlinePool,
    )
    assert [step[0] for step in order] == ["extract", "extract", "match"]
    assert matches[0].i == 0 and matches[0].j == 1


def test_overlap_skips_cached_features_and_keeps_pair_order(monkeypatch, tmp_path: Path):
    from ms_mosaic import runner

    cache = tmp_path / "cache"
    cache.mkdir()
    for name in ("a", "b", "c", "d"):
        (cache / f"{name}.npz").write_bytes(b"")
    order = []

    def fake_extract(job):
        order.append(("extract", int(job[0])))
        return int(job[0])

    def fake_group(indexed):
        out = []
        for orig, job in indexed:
            order.append(("match", orig))
            out.append((orig, (int(job[0]), int(job[1]), np.zeros((1, 2)), "essential")))
        return out

    monkeypatch.setattr(runner, "_extract_worker", fake_extract)
    monkeypatch.setattr(runner, "_match_group", fake_group)
    jobs = [
        (i, str(tmp_path / f"{name}.tif"), str(cache), 32)
        for i, name in enumerate("abcd")
    ]
    pairs = [
        (0, 1, jobs[0][1], jobs[1][1], "Color"),
        (2, 3, jobs[2][1], jobs[3][1], "Color"),
    ]
    matches, feat_s = runner._overlap_extract_match(
        jobs,
        pairs,
        1,
        cache,
        {},
        max_features=32,
        log=lambda *_a: None,
        pool_factory=_InlinePool,
    )
    assert not any(step[0] == "extract" for step in order)
    assert feat_s == 0.0
    assert [(m.i, m.j) for m in matches] == [(0, 1), (2, 3)]
