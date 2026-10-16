from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.auth import require_roles
from app.config import Settings, get_settings
from app.models import User

router = APIRouter(prefix="/api/docs", tags=["docs"])


def _read_doc(settings: Settings, filename: str, configured: Path | None = None) -> str:
    root = Path(__file__).resolve().parents[3] / "worker" / "docs"
    candidates = [
        configured,
        Path(settings.cli_guide_path).parent / filename if settings.cli_guide_path else None,
        root / filename,
        Path("/cli_docs") / filename,
    ]
    for path in candidates:
        if path and path.is_file():
            return path.read_text(encoding="utf-8")
    raise HTTPException(404, f"{filename} 未找到")


@router.get("/cli-guide", response_class=PlainTextResponse)
def cli_guide(
    settings: Settings = Depends(get_settings),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> str:
    configured = Path(settings.cli_guide_path) if settings.cli_guide_path else None
    return _read_doc(settings, "cli-usage.md", configured)


@router.get("/param-guide", response_class=PlainTextResponse)
def param_guide(
    settings: Settings = Depends(get_settings),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> str:
    return _read_doc(settings, "参数说明.md")


@router.get("/quality-guide", response_class=PlainTextResponse)
def quality_guide(
    settings: Settings = Depends(get_settings),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> str:
    return _read_doc(settings, "质量报告手册.md")
