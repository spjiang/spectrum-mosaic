"""平差阶段 3 开关：默认做满三阶段，关闭后停在 k1/k2。"""

from ms_mosaic.at import CALIBRATION_STAGES, calibration_stages


def test_stage3_on_keeps_all_three_stages():
    stages = calibration_stages(True)
    assert stages == CALIBRATION_STAGES
    assert stages[-1] == ("f", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "b1", "b2")


def test_stage3_off_stops_after_radial_k2():
    stages = calibration_stages(False)
    assert stages == CALIBRATION_STAGES[:2]
    names = {name for stage in stages for name in stage}
    assert "k1" in names and "k2" in names
    assert "k3" not in names
    assert "p1" not in names and "b1" not in names


def test_resolve_sparse_forwards_stage3_switch(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_run(_inp, _cache, **kwargs):
        seen.update(kwargs)
        return {"at": None}

    monkeypatch.setattr("ms_mosaic.checkpoint.save_sparse", lambda *_a, **_k: None)
    from ms_mosaic.checkpoint import resolve_sparse

    resolve_sparse(
        tmp_path,
        tmp_path / "cache",
        ba_stage3=False,
        run_sparse_fn=fake_run,
        log=lambda *_a, **_k: None,
    )
    assert seen["ba_stage3"] is False
