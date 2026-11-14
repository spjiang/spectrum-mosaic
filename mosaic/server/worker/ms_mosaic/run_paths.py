"""任务输出目录：与可视化平台相同，在输出根下新建 YYYYMMDD_HHMMSS。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CST = timezone(timedelta(hours=8))
_RUN_STAMP = re.compile(r"^\d{8}_\d{6}$")


def stamp_run_output_dir(output_dir: str | Path, when: datetime | None = None) -> Path:
    """每次任务在输出根目录下新建日期子目录，避免沿用上次缓存。已带时间戳则原样返回。"""
    raw = str(output_dir or "").strip().rstrip("/")
    if not raw:
        return Path(raw)
    p = Path(raw)
    if _RUN_STAMP.match(p.name):
        return p
    stamp = (when or datetime.now(_CST)).strftime("%Y%m%d_%H%M%S")
    return p / stamp
