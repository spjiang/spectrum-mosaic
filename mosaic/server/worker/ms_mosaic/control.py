from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ControlState:
    """文件型控制：worker 写 control.json，编排层轮询。"""

    path: Path | None = None
    _pause: bool = False
    _cancel: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def request_pause(self) -> None:
        with self._lock:
            self._pause = True
            self._flush()

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel = True
            self._flush()

    def clear_pause(self) -> None:
        with self._lock:
            self._pause = False
            self._flush()

    def _flush(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"pause": self._pause, "cancel": self._cancel}),
            encoding="utf-8",
        )

    def _reload(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._pause = bool(data.get("pause"))
            self._cancel = bool(data.get("cancel"))
        except Exception:  # noqa: BLE001
            return

    def checkpoint_barrier(self, *, wait: bool = False) -> str | None:
        """阶段边界。cancel → 'cancel'；pause 且 wait=False → 'pause'；wait=True 时阻塞到恢复。"""
        while True:
            with self._lock:
                self._reload()
                if self._cancel:
                    return "cancel"
                paused = self._pause
            if not paused:
                return None
            if not wait:
                return "pause"
            time.sleep(0.5)
