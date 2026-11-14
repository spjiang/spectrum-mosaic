from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ProgressReporter(Protocol):
    def stage_start(self, stage_id: str, message: str = "") -> None: ...

    def progress(
        self,
        stage_id: str,
        stage_progress: float,
        global_percent: float,
        message: str = "",
        eta_seconds: int | None = None,
    ) -> None: ...

    def stage_done(self, stage_id: str, elapsed_s: float = 0.0) -> None: ...

    def event(self, event: str, **payload: Any) -> None: ...


@dataclass
class NullReporter:
    def stage_start(self, stage_id: str, message: str = "") -> None:
        print(f"[stage] start {stage_id} {message}")

    def progress(
        self,
        stage_id: str,
        stage_progress: float,
        global_percent: float,
        message: str = "",
        eta_seconds: int | None = None,
    ) -> None:
        print(f"[stage] {stage_id} {stage_progress:.1f}% global={global_percent:.1f}% {message}")

    def stage_done(self, stage_id: str, elapsed_s: float = 0.0) -> None:
        print(f"[stage] done {stage_id} {elapsed_s:.1f}s")

    def event(self, event: str, **payload: Any) -> None:
        print(f"[event] {event} {payload}")


STAGES = ["S0_io", "S1_catalog", "S2_at", "S3_dense", "S4_dsm", "S5_ortho", "S6_report"]


def stage_index(stage_id: str) -> int:
    return STAGES.index(stage_id)


def global_percent_for(stage_id: str, stage_progress: float) -> float:
    i = stage_index(stage_id)
    return (i + max(0.0, min(100.0, stage_progress)) / 100.0) / len(STAGES) * 100.0
