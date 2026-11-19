from __future__ import annotations

import os
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



_USER_DIR = re.compile(r"^[A-Za-z0-9_-]+$")


# 与 release/build/Dockerfile.worker-customer 的 useradd -u 10001、start.sh 的 WORKER_UID 一致
WORKER_UID = 10001
WORKER_GID = 10001


def ensure_worker_tree(
    path: Path,
    *,
    stop: Path,
    uid: int = WORKER_UID,
    gid: int = WORKER_GID,
) -> None:
    """创建 path，并把 stop 之下的每一层交给 worker。

    stop 本身不动（通常是 /data）。能把属主改成 worker 时用 0755。
    Windows 绑定挂载常忽略 chown，属主仍是 root 时改为 0777，否则 worker 写不了任务目录。
    """
    path.mkdir(parents=True, exist_ok=True)
    current = path.resolve()
    boundary = stop.resolve()
    chain: list[Path] = []
    while True:
        if current != boundary:
            chain.append(current)
        if current == boundary:
            break
        parent = current.parent
        if parent == current or (boundary not in parent.parents and parent != boundary):
            break
        current = parent
    for directory in reversed(chain):
        try:
            os.chown(directory, uid, gid)
        except PermissionError:
            directory.chmod(0o777)
            continue
        if directory.stat().st_uid == uid:
            directory.chmod(0o755)
        else:
            directory.chmod(0o777)


def prepare_job_dirs(*paths: str, stop: Path | None = None) -> None:
    """任务目录在投递 worker 之前建好，并允许 uid 10001 写入。"""
    boundary = stop or Path("/data")
    for raw in paths:
        if raw:
            ensure_worker_tree(Path(raw), stop=boundary)


def trial_dirs(username: str) -> tuple[str, str]:
    """试用账号只能使用同名的输入目录和成果目录。"""
    if not _USER_DIR.match(username or ""):
        raise HTTPException(400, "当前账号不能对应数据目录")
    return f"/data/input/{username}", f"/data/output/runs/{username}"


def _inside(path: str, root: str) -> bool:
    text = (path or "").strip().rstrip("/")
    base = root.rstrip("/")
    if not text or ".." in Path(text).parts:
        return False
    return text == base or text.startswith(base + "/")


def assert_owned_dirs(username: str, input_dir: str, output_dir: str, *, is_admin: bool) -> None:
    """管理员不限。其它账号的测区和成果必须落在自己的目录下，不能改别人的数据。"""
    if is_admin:
        return
    in_root, out_root = trial_dirs(username)
    if not _inside(input_dir, in_root):
        raise HTTPException(400, f"测区数据只能使用 {in_root} 及其子目录")
    if not _inside(output_dir, out_root):
        raise HTTPException(400, f"成果目录只能使用 {out_root} 及其子目录")


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
