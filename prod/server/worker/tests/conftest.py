import pytest


def _gpu_ready() -> bool:
    from ms_mosaic.gpu import probe

    gpus, _ = probe()
    return bool(gpus)


def pytest_collection_modifyitems(config, items):
    if not any("gpu" in item.keywords for item in items):
        return
    if _gpu_ready():
        return
    skip = pytest.mark.skip(reason="没有 CuPy 或可用显卡")
    for item in items:
        if "gpu" in item.keywords:
            item.add_marker(skip)
