"""任务消息校验与任务目录里的控制文件、结束标记。worker 主进程和子进程共用。

- log/control.json：后端写 {"cancel": bool, "run_attempt": n}，消息丢了也能取消。
- log/attempt-<n>.done：这一次运行已结束（成功、失败或取消），重投的消息据此跳过。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA = 1
DEFAULT_INPUT_ROOTS = "/data/input"
DEFAULT_OUTPUT_ROOTS = "/data/output"


class JobMessageError(ValueError):
    pass


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    run_attempt: int
    input_dir: Path
    output_dir: Path
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def log_dir(self) -> Path:
        return self.output_dir / "log"

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_attempt": self.run_attempt,
            "input_dir": str(self.input_dir),
            "output_dir": str(self.output_dir),
        }


def _roots(env: str, default: str) -> list[Path]:
    raw = os.environ.get(env) or default
    return [Path(p).resolve() for p in raw.split(":") if p.strip()]


def _under(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


def parse_job(payload: Any) -> JobSpec:
    if not isinstance(payload, dict):
        raise JobMessageError("消息不是 JSON 对象")
    schema = payload.get("schema_version")
    if schema != SUPPORTED_SCHEMA:
        raise JobMessageError(f"不支持的消息版本 {schema!r}，本 worker 支持 {SUPPORTED_SCHEMA}")
    try:
        job_id = str(uuid.UUID(str(payload.get("job_id"))))
    except ValueError as exc:
        raise JobMessageError("job_id 不是 UUID") from exc
    try:
        attempt = int(payload.get("run_attempt"))
    except (TypeError, ValueError) as exc:
        raise JobMessageError("run_attempt 不是整数") from exc
    if attempt < 1:
        raise JobMessageError("run_attempt 必须从 1 开始")
    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JobMessageError("params 不是对象")
    raw_in, raw_out = payload.get("input_dir"), payload.get("output_dir")
    if not raw_in or not raw_out:
        raise JobMessageError("缺少 input_dir 或 output_dir")
    inp, out = Path(str(raw_in)).resolve(), Path(str(raw_out)).resolve()
    in_roots = _roots("PB_INPUT_ROOTS", DEFAULT_INPUT_ROOTS)
    out_roots = _roots("PB_OUTPUT_ROOTS", DEFAULT_OUTPUT_ROOTS)
    if not _under(inp, in_roots):
        raise JobMessageError(f"输入目录不在允许的范围内：{inp}")
    if not _under(out, out_roots) or out in out_roots:
        raise JobMessageError(f"输出目录不在允许的范围内：{out}")
    return JobSpec(job_id, attempt, inp, out, dict(params))


def cancel_requested(job: JobSpec) -> bool:
    path = job.log_dir / "control.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not data.get("cancel"):
        return False
    try:
        return int(data.get("run_attempt") or 0) == job.run_attempt
    except (TypeError, ValueError):
        return False


def done_marker(job: JobSpec) -> Path:
    return job.log_dir / f"attempt-{job.run_attempt}.done"


def is_done(job: JobSpec) -> bool:
    return done_marker(job).is_file()


def mark_done(job: JobSpec, status: str, message: str = "") -> None:
    path = done_marker(job)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(
                {"status": status, "message": message, "at": datetime.now(timezone.utc).isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass
