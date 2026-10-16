from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import Settings

_CST = timezone(timedelta(hours=8))
_RUN_STAMP = re.compile(r"^\d{8}_\d{6}$")
_DERIVED_DIRS = ("cache_dir", "log_dir", "process_dir")


def stamp_run_output_dir(output_dir: str, when: datetime | None = None) -> str:
    """每次任务在输出根目录下新建 YYYYMMDD_HHMMSS 子目录，避免沿用上次缓存。"""
    raw = (output_dir or "").strip().rstrip("/")
    if not raw:
        return raw
    p = Path(raw)
    if _RUN_STAMP.match(p.name):
        return raw
    stamp = (when or datetime.now(_CST)).strftime("%Y%m%d_%H%M%S")
    return str(p / stamp)


def detach_derived_dirs(params: dict, original_output: str) -> None:
    """方案里若写死了旧输出下的 cache/log，跟到新的日期目录。"""
    base = str(Path((original_output or "").strip().rstrip("/") or "."))
    for key in _DERIVED_DIRS:
        val = params.get(key)
        if not val:
            params[key] = None
            continue
        text = str(val).rstrip("/")
        if text == base or text.startswith(base + "/"):
            params[key] = None



def validate_paths(settings: Settings, input_dir: str, output_dir: str) -> tuple[Path, Path]:
    inp = Path(input_dir).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    if out == inp or inp in out.parents:
        raise HTTPException(400, "输出目录不能落在输入目录内")
    roots = [Path(r).expanduser().resolve() for r in settings.data_root_list()]
    if roots:

        def under(p: Path) -> bool:
            return any(p == r or r in p.parents for r in roots)

        if not under(inp) or not under(out):
            raise HTTPException(400, f"路径必须位于 DATA_ROOTS 下: {settings.data_roots}")
    return inp, out
