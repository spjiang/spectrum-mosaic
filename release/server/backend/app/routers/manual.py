from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.auth import get_current_admin
from app.models import Admin

router = APIRouter(prefix="/api/manual", tags=["manual"])

TITLE = "拼图服务操作手册"
INTERNAL_TITLE = "发版台内部操作手册"
INTERNAL_FILE = "internal-manual.md"
INTERNAL_DOWNLOAD_STEM = "内部操作手册"
CUSTOMER_DOCS = (
    ("deploy", "拼图服务私有部署手册", "拼图服务私有部署操作手册.md"),
    ("web", "拼图服务WEB操作手册", "拼图服务WEB操作手册.md"),
)


def _docs_dir() -> Path:
    candidates = [Path("/workspace/release/docs")]
    here = Path(__file__).resolve()
    # Local: .../release/server/backend/app/routers/manual.py → release/docs
    for up in range(1, len(here.parents)):
        root = here.parents[up]
        candidates.append(root / "docs")
        candidates.append(root / "release" / "docs")
    for path in candidates:
        if (path / INTERNAL_FILE).exists():
            return path
    return candidates[0]


def _customer_docs_dir() -> Path:
    """与打交付包相同：仓库 docs/操作文档 下的两份拼图服务手册。"""
    candidates = [Path("/workspace/docs/操作文档")]
    here = Path(__file__).resolve()
    for up in range(1, len(here.parents)):
        candidates.append(here.parents[up] / "docs" / "操作文档")
    for path in candidates:
        if all((path / name).exists() for _key, _title, name in CUSTOMER_DOCS):
            return path
    return candidates[0]


def _customer_doc(doc: str) -> tuple[str, str, str]:
    for key, title, filename in CUSTOMER_DOCS:
        if key == doc:
            return key, title, filename
    raise HTTPException(status_code=400, detail="手册不存在")


def _read_customer_manual(doc: str) -> tuple[str, str, str]:
    _key, title, filename = _customer_doc(doc)
    path = _customer_docs_dir() / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"缺少{filename}")
    return title, filename, path.read_text(encoding="utf-8")


class ManualDoc(BaseModel):
    key: str
    title: str
    filename: str
    markdown: str


class ManualMeta(BaseModel):
    title: str
    markdown: str
    has_pdf: bool = False
    docs: list[ManualDoc] = []


@router.get("", response_model=ManualMeta)
def get_manual(_: Admin = Depends(get_current_admin)) -> ManualMeta:
    docs: list[ManualDoc] = []
    for key, _title, _filename in CUSTOMER_DOCS:
        title, filename, markdown = _read_customer_manual(key)
        docs.append(ManualDoc(key=key, title=title, filename=filename, markdown=markdown))
    return ManualMeta(title=TITLE, markdown="", has_pdf=False, docs=docs)


@router.get("/download")
def download_manual(
    doc: str = Query("deploy", pattern="^(deploy|web)$"),
    _: Admin = Depends(get_current_admin),
):
    _title, filename, text = _read_customer_manual(doc)
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
