from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_access_token, verify_password
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Admin
from app.schemas import LoginIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> TokenOut:
    admin = db.scalar(select(Admin).where(Admin.username == body.username))
    if admin is None or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    return TokenOut(access_token=create_access_token(admin.username, settings))
