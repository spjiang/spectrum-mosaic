from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.db import get_db
from app.models import User
from app.services.audit import record_audit
from app.services.rbac import MENU_KEYS, ROLES, load_mapping, set_permission_roles, set_role_menus

router = APIRouter(prefix="/api/rbac", tags=["rbac"])


class RbacOut(BaseModel):
    menus: dict[str, list[str]]


class RoleMenusIn(BaseModel):
    menus: list[str] = Field(default_factory=list)


class PermRolesIn(BaseModel):
    roles: list[str] = Field(default_factory=list)


@router.get("", response_model=RbacOut)
def get_rbac(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin", "configurator", "executor", "viewer")),
) -> RbacOut:
    return RbacOut(menus=load_mapping(db))


@router.put("/roles/{role}", response_model=RbacOut)
def update_role_menus(
    role: str,
    body: RoleMenusIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> RbacOut:
    if role not in ROLES:
        raise HTTPException(400, "角色不存在")
    menus = set_role_menus(db, role, body.menus)
    record_audit(db, user.id, "rbac.roles", {"role": role, "menus": menus.get(role, [])})
    return RbacOut(menus=menus)


@router.put("/permissions/{key}", response_model=RbacOut)
def update_permission_roles(
    key: str,
    body: PermRolesIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
) -> RbacOut:
    if key not in MENU_KEYS:
        raise HTTPException(400, "权限不存在")
    menus = set_permission_roles(db, key, body.roles)
    record_audit(db, user.id, "rbac.permissions", {"key": key, "roles": body.roles})
    return RbacOut(menus=menus)
