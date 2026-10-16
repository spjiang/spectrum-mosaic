from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import authenticate_user, create_access_token
from app.config import Settings, get_settings
from app.db import get_db
from app.schemas import TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    user = authenticate_user(db, form.username, form.password)
    if user is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="用户名或密码错误")
    roles = [r.name for r in user.roles]
    token = create_access_token(user.username, roles, settings)
    return TokenOut(access_token=token, roles=roles)
