from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import SystemSetting

CLI_SETTING_KEY = "cli"


def _env_defaults(settings: Settings) -> dict[str, str]:
    python = (settings.cli_python or "").strip() or sys.executable
    module = (settings.cli_module or "ms_mosaic").strip() or "ms_mosaic"
    cwd = (settings.cli_cwd or "").strip()
    if not cwd:
        cwd = "/app"
    probe_args = (settings.cli_probe_args or "-h").strip() or "-h"
    return {"python": python, "module": module, "cwd": cwd, "probe_args": probe_args}


def load_cli_override(db: Session | None) -> dict[str, Any] | None:
    if db is None:
        return None
    row = db.get(SystemSetting, CLI_SETTING_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    return row.value


def save_cli_override(db: Session, payload: dict[str, str]) -> dict[str, str]:
    cleaned = {
        "python": (payload.get("python") or "").strip(),
        "module": (payload.get("module") or "").strip() or "ms_mosaic",
        "cwd": (payload.get("cwd") or "").strip(),
        "probe_args": (payload.get("probe_args") or "").strip() or "-h",
    }
    row = db.get(SystemSetting, CLI_SETTING_KEY)
    if row is None:
        row = SystemSetting(key=CLI_SETTING_KEY, value=cleaned)
        db.add(row)
    else:
        row.value = cleaned
    db.commit()
    db.refresh(row)
    return cleaned


def resolve_cli(settings: Settings, db: Session | None = None, override: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = _env_defaults(settings)
    ov = dict(override if override is not None else (load_cli_override(db) or {}))
    cwd_ov = (ov.get("cwd") or "").strip()
    if cwd_ov and not Path(cwd_ov).is_dir():
        ov["cwd"] = ""
    py_ov = (ov.get("python") or "").strip()
    if py_ov and not Path(py_ov).exists():
        ov["python"] = ""
    source = "db" if any(str(ov.get(k) or "").strip() for k in ("python", "module", "cwd", "probe_args")) else "env"

    python = (ov.get("python") or "").strip() or defaults["python"]
    if python and not Path(python).exists():
        python = "python"
    module = (ov.get("module") or "").strip() or defaults["module"]
    cwd = (ov.get("cwd") or "").strip() or defaults["cwd"]
    probe_raw = (ov.get("probe_args") or "").strip() or defaults["probe_args"]
    probe = shlex.split(probe_raw)
    command = [python, "-m", module, *probe]
    return {
        "python": python,
        "module": module,
        "cwd": cwd,
        "probe_args": probe_raw,
        "command": " ".join(shlex.quote(c) for c in command),
        "argv": command,
        "source": source,
    }


def check_cli(
    settings: Settings,
    db: Session | None = None,
    override: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> tuple[str, dict[str, Any]]:
    """可调用探测：按配置跑 probe（默认 -h）。返回 (status, cli_config)。"""
    cfg = resolve_cli(settings, db=db, override=override)
    summary = {
        "python": cfg["python"],
        "module": cfg["module"],
        "cwd": cfg["cwd"],
        "probe_args": cfg["probe_args"],
        "command": cfg["command"],
        "source": cfg["source"],
    }
    cwd = Path(cfg["cwd"])
    if not cwd.is_dir():
        return f"error: CLI_CWD 不存在: {cwd}", summary
    try:
        proc = subprocess.run(
            cfg["argv"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "error: 探测超时", summary
    except OSError as exc:
        return f"error: {exc}", summary

    if proc.returncode == 0:
        return "ok", summary

    err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    if len(err) > 500:
        err = err[:500] + "…"
    return f"error: {err}", summary


def check_program_dir(
    settings: Settings,
    db: Session | None = None,
    override: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """主程序在 server/worker：确认目录和 ms_mosaic 包在，不在 backend 里跑本机 python。"""
    cfg = resolve_cli(settings, db=db, override=override)
    summary = {
        "python": cfg["python"],
        "module": cfg["module"],
        "cwd": cfg["cwd"],
        "probe_args": cfg["probe_args"],
        "command": f"主程序目录 {cfg['cwd']}",
        "source": cfg["source"],
    }
    cwd = Path(cfg["cwd"])
    if not cwd.is_dir():
        return f"error: 主程序目录不存在: {cwd}", summary
    pkg = cwd / "ms_mosaic" / "__init__.py"
    if not pkg.is_file():
        return f"error: 未找到 ms_mosaic 包: {cwd / 'ms_mosaic'}", summary
    return "ok", summary
