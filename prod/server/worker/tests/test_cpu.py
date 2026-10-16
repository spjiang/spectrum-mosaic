from ms_mosaic.cpu import parse_cpu_max, resolve_cpu_budget, set_job_cpus, job_cpus, cpu_cap


def test_parse_cpu_max_unlimited():
    assert parse_cpu_max("max 100000") is None
    assert parse_cpu_max("max") is None


def test_parse_cpu_max_quota():
    assert parse_cpu_max("1500000 100000") == 15
    assert parse_cpu_max("500000 100000") == 5


def test_resolve_cpu_budget_auto():
    assert resolve_cpu_budget(0) is None
    assert resolve_cpu_budget(None) is None


def test_resolve_cpu_budget_clamps_to_visible(monkeypatch):
    monkeypatch.setattr("ms_mosaic.cpu.visible_cpu_count", lambda: 15)
    assert resolve_cpu_budget(32) == 15
    assert resolve_cpu_budget(8) == 8


def test_job_cpus_context():
    set_job_cpus(6)
    try:
        assert job_cpus() == 6
    finally:
        set_job_cpus(None)
    assert job_cpus() is None


def test_cpu_cap_uses_job_budget(monkeypatch):
    monkeypatch.setattr("ms_mosaic.cpu.visible_cpu_count", lambda: 15)
    set_job_cpus(4)
    try:
        assert cpu_cap() == 4
    finally:
        set_job_cpus(None)
