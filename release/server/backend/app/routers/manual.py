from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from app.auth import get_current_admin
from app.models import Admin

router = APIRouter(prefix="/api/manual", tags=["manual"])

TITLE = "高光谱拼图 · 客户操作手册"
DOWNLOAD_STEM = "操作手册"
INTERNAL_TITLE = "发版台内部操作手册"
INTERNAL_FILE = "internal-manual.md"
INTERNAL_DOWNLOAD_STEM = "内部操作手册"


def _docs_dir() -> Path:
    candidates = [Path("/workspace/release/docs")]
    here = Path(__file__).resolve()
    # Local: .../release/server/backend/app/routers/manual.py → release/docs
    for up in range(1, len(here.parents)):
        root = here.parents[up]
        candidates.append(root / "docs")
        candidates.append(root / "release" / "docs")
    for path in candidates:
        if (path / "customer-manual.md").exists():
            return path
    return candidates[0]


class ManualMeta(BaseModel):
    title: str
    markdown: str
    has_pdf: bool


@router.get("", response_model=ManualMeta)
def get_manual(_: Admin = Depends(get_current_admin)) -> ManualMeta:
    md = _docs_dir() / "customer-manual.md"
    if not md.exists():
        raise HTTPException(status_code=404, detail="操作手册不存在")
    pdf = _docs_dir() / "customer-manual.pdf"
    return ManualMeta(title=TITLE, markdown=md.read_text(encoding="utf-8"), has_pdf=pdf.exists())


@router.get("/download")
def download_manual(
    format: str = Query("md", pattern="^(md|pdf)$"),
    _: Admin = Depends(get_current_admin),
):
    docs = _docs_dir()
    if format == "md":
        path = docs / "customer-manual.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail="操作手册 Markdown 不存在")
        return PlainTextResponse(
            path.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{DOWNLOAD_STEM}.md"'},
        )
    path = docs / "customer-manual.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="操作手册 PDF 不存在")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{DOWNLOAD_STEM}.pdf",
    )


@router.get("/internal", response_model=ManualMeta)
def get_internal_manual(_: Admin = Depends(get_current_admin)) -> ManualMeta:
    md = _docs_dir() / INTERNAL_FILE
    if not md.exists():
        raise HTTPException(status_code=404, detail="内部操作手册不存在")
    return ManualMeta(title=INTERNAL_TITLE, markdown=md.read_text(encoding="utf-8"), has_pdf=False)


@router.get("/internal/download")
def download_internal_manual(_: Admin = Depends(get_current_admin)):
    path = _docs_dir() / INTERNAL_FILE
    if not path.exists():
        raise HTTPException(status_code=404, detail="内部操作手册不存在")
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{INTERNAL_DOWNLOAD_STEM}.md"'},
    )
