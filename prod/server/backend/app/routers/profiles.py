from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.db import get_db
from app.models import ParamDefinition, ParamProfile, ParamProfileValue, User
from app.schemas import ParamDefOut, ProfileCreate, ProfileOut, ProfileUpdate
from app.presets import RGB_PREVIEW_VALUES
from app.services.audit import record_audit
from app.services.params import assert_param_values

router = APIRouter(prefix="/api", tags=["profiles"])


@router.get("/param-definitions", response_model=list[ParamDefOut])
def list_defs(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("configurator", "executor", "viewer", "admin")),
) -> list[ParamDefinition]:
    return list(db.scalars(select(ParamDefinition).order_by(ParamDefinition.stage_id, ParamDefinition.sort_order)))


def _profile_out(db: Session, p: ParamProfile, values: dict[str, Any] | None = None) -> ProfileOut:
    if values is None:
        rows = db.scalars(select(ParamProfileValue).where(ParamProfileValue.profile_id == p.id)).all()
        values = {r.param_key: r.value for r in rows}
    return ProfileOut(
        id=p.id,
        name=p.name,
        description=p.description,
        version=p.version,
        preset=p.preset,
        values=values,
        updated_at=p.updated_at,
    )


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("configurator", "executor", "viewer", "admin")),
) -> list[ProfileOut]:
    profiles = list(db.scalars(select(ParamProfile).order_by(ParamProfile.id)))
    if not profiles:
        return []
    ids = [p.id for p in profiles]
    rows = db.scalars(select(ParamProfileValue).where(ParamProfileValue.profile_id.in_(ids))).all()
    by_pid: dict[int, dict[str, Any]] = {i: {} for i in ids}
    for r in rows:
        by_pid[r.profile_id][r.param_key] = r.value
    return [_profile_out(db, p, by_pid.get(p.id, {})) for p in profiles]

@router.post("/profiles", response_model=ProfileOut)
def create_profile(
    body: ProfileCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("configurator", "admin")),
) -> ProfileOut:
    if db.scalar(select(ParamProfile).where(ParamProfile.name == body.name)):
        raise HTTPException(400, "模版名已存在")
    values = dict(body.values)
    if body.preset == "rgb_preview":
        values.update(RGB_PREVIEW_VALUES)
    assert_param_values(list(db.scalars(select(ParamDefinition))), values)
    p = ParamProfile(name=body.name, description=body.description, preset=body.preset, created_by=user.id)
    db.add(p)
    db.flush()
    for k, v in values.items():
        if db.get(ParamDefinition, k) is None:
            continue
        db.add(ParamProfileValue(profile_id=p.id, param_key=k, value=v))
    db.commit()
    db.refresh(p)
    record_audit(db, user.id, "profile.create", {"id": p.id, "name": p.name, "preset": p.preset})
    return _profile_out(db, p)


@router.put("/profiles/{profile_id}", response_model=ProfileOut)
def update_profile(
    profile_id: int,
    body: ProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("configurator", "admin")),
) -> ProfileOut:
    p = db.get(ParamProfile, profile_id)
    if p is None:
        raise HTTPException(404, "参数模版不存在")
    if body.name is not None:
        new_name = body.name.strip()
        if not new_name:
            raise HTTPException(400, "模版名称不能为空")
        if new_name != p.name:
            clash = db.scalar(
                select(ParamProfile).where(ParamProfile.name == new_name, ParamProfile.id != p.id)
            )
            if clash is not None:
                raise HTTPException(400, "模版名已存在")
            p.name = new_name
    if body.description is not None:
        p.description = body.description
    if body.preset is not None:
        p.preset = body.preset
    if body.values is not None:
        values = dict(body.values)
        if p.preset == "rgb_preview":
            values.update(RGB_PREVIEW_VALUES)
        assert_param_values(list(db.scalars(select(ParamDefinition))), values)
        db.execute(delete(ParamProfileValue).where(ParamProfileValue.profile_id == p.id))
        for k, v in values.items():
            if db.get(ParamDefinition, k) is None:
                continue
            db.add(ParamProfileValue(profile_id=p.id, param_key=k, value=v))
        p.version += 1
    db.commit()
    db.refresh(p)
    record_audit(db, user.id, "profile.update", {"id": p.id, "name": p.name, "version": p.version})
    return _profile_out(db, p)
