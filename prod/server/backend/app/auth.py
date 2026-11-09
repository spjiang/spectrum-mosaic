from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Role, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, roles: list[str], settings: Settings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": subject, "roles": roles, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(
        select(User).options(selectinload(User.roles)).where(User.username == username)
    )


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """只核对用户名和密码。停用、到期由 access_block_reason 另报，避免和密码错误混成一句。"""
    user = get_user_by_username(db, username)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def access_block_reason(user: User, *, now: datetime | None = None) -> str | None:
    """账号此刻不能进系统的原因。空表示可以。到期时刻本身已经不能用。"""
    if not user.is_active:
        return "账号已停用"
    expires_at = getattr(user, "expires_at", None)
    if expires_at is None:
        return None
    current = _as_utc(now or datetime.now(timezone.utc))
    if current >= _as_utc(expires_at):
        return "试用已到期"
    return None


def owner_may_autostart(user_id: int | None, user: User | None, *, now: datetime | None = None) -> bool:
    """增量测区到点自动开拼图。没有归属人的旧测区照旧；归属人停用或到期则不再开。"""
    if user_id is None:
        return True
    if user is None:
        return False
    return access_block_reason(user, now=now) is None


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    cred_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效凭证")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
        if not username:
            raise cred_exc
    except JWTError as exc:
        raise cred_exc from exc
    try:
        user = get_user_by_username(db, username)
    except OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"数据库暂时连不上: {exc}") from exc
    if user is None:
        raise cred_exc
    reason = access_block_reason(user)
    if reason:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=reason)
    return user


def is_admin(user: User) -> bool:
    return any(role.name == "admin" for role in user.roles)


def owns_record(user: User, created_by: int | None, *, shared_if_unset: bool = False) -> bool:
    """管理员看全部。其他人只看自己创建的记录。没有归属人的旧任务不开放；方案可以共享未归属的模板。"""
    if is_admin(user):
        return True
    if created_by is None:
        return shared_if_unset
    return created_by == user.id


def require_roles(*allowed: str):
    async def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        names = {r.name for r in user.roles}
        if "admin" in names:
            return user
        if not names.intersection(allowed):
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return _dep


def ensure_role(db: Session, name: str) -> Role:
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(name=name)
        db.add(role)
        db.flush()
    return role
