from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import ensure_role, hash_password, require_roles
from app.db import get_db
from app.models import User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/api/users", tags=["users"])

ALLOWED = ("admin", "configurator", "executor", "viewer")


def _out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        is_active=user.is_active,
        roles=[r.name for r in user.roles],
        created_at=user.created_at,
    )


def _set_roles(db: Session, user: User, names: list[str]) -> None:
    cleaned = [n for n in names if n in ALLOWED]
    if not cleaned:
        raise HTTPException(400, "请至少指定一个角色")
    user.roles = [ensure_role(db, n) for n in cleaned]


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
) -> list[UserOut]:
    rows = db.scalars(select(User).options(selectinload(User.roles)).order_by(User.id)).all()
    return [_out(u) for u in rows]


@router.post("", response_model=UserOut)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("admin")),
) -> UserOut:
    name = body.username.strip()
    if not name or not body.password:
        raise HTTPException(400, "用户名和密码不能为空")
    exists = db.scalar(select(User).where(User.username == name))
    if exists is not None:
        raise HTTPException(409, "用户名已存在")
    user = User(username=name, password_hash=hash_password(body.password), is_active=True)
    _set_roles(db, user, body.roles)
    db.add(user)
    db.commit()
    db.refresh(user)
    user = db.scalar(select(User).options(selectinload(User.roles)).where(User.id == user.id))
    record_audit(db, actor.id, "user.create", {"id": user.id, "username": user.username, "roles": [r.name for r in user.roles]})
    return _out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles("admin")),
) -> UserOut:
    user = db.scalar(select(User).options(selectinload(User.roles)).where(User.id == user_id))
    if user is None:
        raise HTTPException(404, "用户不存在")
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        if user.id == actor.id and body.is_active is False:
            raise HTTPException(400, "无法停用当前登录账号")
        user.is_active = body.is_active
    if body.roles is not None:
        if user.id == actor.id and "admin" not in body.roles:
            raise HTTPException(400, "无法取消当前账号的系统管理员角色")
        _set_roles(db, user, body.roles)
    db.commit()
    user = db.scalar(select(User).options(selectinload(User.roles)).where(User.id == user_id))
    record_audit(
        db,
        actor.id,
        "user.update",
        {
            "id": user.id,
            "username": user.username,
            "roles": [r.name for r in user.roles],
            "is_active": user.is_active,
            "password_changed": bool(body.password),
        },
    )
    return _out(user)
