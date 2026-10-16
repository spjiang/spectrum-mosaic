from ms_mosaic.memory import cap_workers, resolve_memory_budget


def test_resolve_memory_budget_auto_uses_engine_limit_not_memavailable(monkeypatch):
    monkeypatch.setattr("ms_mosaic.memory.visible_limit_bytes", lambda: int(40.0 * 1024**3))
    assert resolve_memory_budget(0) == int(40.0 * 1024**3 * 0.9)
    assert resolve_memory_budget(None) == int(40.0 * 1024**3 * 0.9)


def test_resolve_memory_budget_auto_none_without_engine_limit(monkeypatch):
    monkeypatch.setattr("ms_mosaic.memory.visible_limit_bytes", lambda: None)
    assert resolve_memory_budget(0) is None
    assert resolve_memory_budget(None) is None


def test_resolve_memory_budget_clamps_above_docker_limit(monkeypatch):
    monkeypatch.setattr("ms_mosaic.memory.visible_limit_bytes", lambda: int(40.3 * 1024**3))
    assert resolve_memory_budget(48) == int(40.3 * 1024**3)


def test_resolve_memory_budget_keeps_value_under_limit(monkeypatch):
    monkeypatch.setattr("ms_mosaic.memory.visible_limit_bytes", lambda: int(40.3 * 1024**3))
    assert resolve_memory_budget(36) == 36 * 1024**3


def test_cap_workers_uses_available_not_just_engine_budget():
    n = cap_workers(
        12,
        per_worker_mb=2000,
        reserve_mb=8192,
        budget_bytes=int(36 * 1024**3),
        available=int(18 * 1024**3),
    )
    assert n == 5


def test_cap_workers_serial_when_available_below_reserve():
    assert (
        cap_workers(
            12,
            per_worker_mb=2000,
            reserve_mb=8192,
            budget_bytes=int(36 * 1024**3),
            available=int(4 * 1024**3),
        )
        == 1
    )


def test_cap_workers_honors_request_when_ram_plenty():
    assert (
        cap_workers(
            3,
            per_worker_mb=2000,
            reserve_mb=8192,
            budget_bytes=int(36 * 1024**3),
            available=int(64 * 1024**3),
        )
        == 3
    )
