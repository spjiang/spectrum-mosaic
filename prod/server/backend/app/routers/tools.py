from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.auth import require_roles
from app.models import User
from app.services.inspect_media import (
    ALLOWED,
    MAX_BYTES,
    create_session_from_path,
    get_session,
    inspect_dir,
    preview_png,
    public_meta,
    sample_pixel,
)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_CHUNK = 1024 * 1024


@router.post("/inspect")
async def inspect_upload(
    file: UploadFile = File(...),
    _: User = Depends(require_roles("admin", "configurator", "executor", "viewer")),
) -> dict:
    name = file.filename or "upload.bin"
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, "仅支持 TIF / TIFF / JPG / JPEG")
    dest = inspect_dir() / f"{uuid.uuid4().hex}{suffix}"
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise ValueError(f"文件超过 {MAX_BYTES // (1024 * 1024)} MB")
                out.write(chunk)
        if written <= 0:
            raise ValueError("文件是空的")
        sess = await asyncio.to_thread(create_session_from_path, name, dest)
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"无法解析该文件: {exc}") from exc
    return public_meta(sess)


@router.get("/inspect/{sid}")
def inspect_meta(
    sid: str,
    _: User = Depends(require_roles("admin", "configurator", "executor", "viewer")),
) -> dict:
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(404, "查看会话已过期，请重新上传")
    return public_meta(sess)


@router.get("/inspect/{sid}/preview")
def inspect_preview(
    sid: str,
    _: User = Depends(require_roles("admin", "configurator", "executor", "viewer")),
) -> Response:
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(404, "查看会话已过期，请重新上传")
    return Response(content=preview_png(sess), media_type="image/png")


@router.get("/inspect/{sid}/pixel")
def inspect_pixel(
    sid: str,
    col: int,
    row: int,
    _: User = Depends(require_roles("admin", "configurator", "executor", "viewer")),
) -> dict:
    sess = get_session(sid)
    if sess is None:
        raise HTTPException(404, "查看会话已过期，请重新上传")
    try:
        return sample_pixel(sess, col, row)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
