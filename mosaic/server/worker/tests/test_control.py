import threading
import time
from pathlib import Path

from ms_mosaic.control import ControlState


def test_checkpoint_returns_pause_without_blocking(tmp_path: Path):
    path = tmp_path / "control.json"
    ctrl = ControlState(path=path)
    ctrl.request_pause()
    t0 = time.time()
    assert ctrl.checkpoint_barrier(wait=False) == "pause"
    assert time.time() - t0 < 0.4


def test_checkpoint_wait_unblocks_on_resume(tmp_path: Path):
    path = tmp_path / "control.json"
    ctrl = ControlState(path=path)
    ctrl.request_pause()
    done = []

    def waiter():
        done.append(ctrl.checkpoint_barrier(wait=True))

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.2)
    assert t.is_alive()
    ctrl.clear_pause()
    t.join(timeout=2)
    assert not t.is_alive()
    assert done == [None]


def test_checkpoint_cancel_wins(tmp_path: Path):
    path = tmp_path / "control.json"
    ctrl = ControlState(path=path)
    ctrl.request_pause()
    ctrl.request_cancel()
    assert ctrl.checkpoint_barrier(wait=False) == "cancel"
    assert ctrl.checkpoint_barrier(wait=True) == "cancel"
