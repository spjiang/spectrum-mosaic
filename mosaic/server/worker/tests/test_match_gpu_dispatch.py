"""匹配主路径只走 CPU FLANN，不再问 GPU。"""

from ms_mosaic import runner


def test_match_all_always_cpu(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_match_all_cpu", lambda *_a, **_k: ["cpu"])
    monkeypatch.setattr(
        runner,
        "_match_all_gpu",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不该走 GPU")),
    )
    logs: list[str] = []
    assert runner._match_all([], 32, tmp_path, {}, log=logs.append) == ["cpu"]
    assert any("CPU FLANN" in line for line in logs)
    assert not any("尚未通过验收" in line for line in logs)
