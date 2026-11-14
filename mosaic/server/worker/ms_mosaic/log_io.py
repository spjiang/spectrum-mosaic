from __future__ import annotations

import re
import time

_HAS_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def stamp_print_line(line: str, now: str | None = None) -> str:
    """给没有时间的 print 行补上本地时间。已带时间戳的（logging）原样留下。"""
    if line == "" or line == "\n":
        return line
    nl = line.endswith("\n")
    body = line[:-1] if nl else line
    if _HAS_TS.match(body.lstrip()):
        return line
    ts = now or time.strftime("%Y-%m-%d %H:%M:%S")
    out = f"{ts} {body}"
    return out + "\n" if nl else out
