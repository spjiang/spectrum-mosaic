from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.bootstrap import system_status_payload, worker_env_payload
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.schemas import SystemStatusOut, WorkerEnvOut, WorkerEnvUpdate, WorkerInspectOut, WorkerKillOut
from app.services import jobs as jobsvc
from app.services.audit import record_audit
from app.services.deps import get_mq
from app.services.mq import MQPublisher
from app.services.worker_env import save_worker_env
from app.services.worker_inspect import inspect_worker

router = APIRouter(prefix="/api/system", tags=["system"])


class WorkerEnvResponse(BaseModel):
    worker_env: WorkerEnvOut
    can_edit: bool = False


def _can_edit(user: User) -> bool:
    names = {r.name for r in user.roles}
    return bool(names.intersection({"admin", "configurator"}))


@router.get("/status", response_model=SystemStatusOut)
def system_status(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> SystemStatusOut:
    """健康检查：PostgreSQL / RabbitMQ / Worker 是否在服务。"""
    try:
        return SystemStatusOut(**system_status_payload(db, settings, can_edit=_can_edit(user)))
    except Exception as exc:  # noqa: BLE001
        return SystemStatusOut(
            status="degraded",
            postgres=f"error: {exc}",
            rabbitmq="skipped",
            worker="skipped",
            can_edit=_can_edit(user),
        )


@router.get("/worker", response_model=WorkerInspectOut)
def worker_inspect(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> dict:
    """Worker 心跳、队列消费者、当前任务和进程树。"""
    return inspect_worker(db, settings)


@router.post("/worker/kill-all", response_model=WorkerKillOut)
def worker_kill_all(
    db: Session = Depends(get_db),
    mq: MQPublisher = Depends(get_mq),
    user: User = Depends(require_roles("executor", "admin")),
) -> WorkerKillOut:
    """立刻杀掉 Worker 计算进程，并取消所有未结束任务。"""
    killed = jobsvc.kill_all_active(db, mq)
    record_audit(db, user.id, "job.kill_all", {"killed": killed})
    return WorkerKillOut(killed=killed)


@router.get("/worker-env", response_model=WorkerEnvResponse)
def get_worker_env(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("executor", "viewer", "admin", "configurator")),
) -> WorkerEnvResponse:
    return WorkerEnvResponse(**worker_env_payload(db, settings, can_edit=_can_edit(user)))


@router.put("/worker-env", response_model=WorkerEnvResponse)
def update_worker_env(
    body: WorkerEnvUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_roles("admin", "configurator")),
) -> WorkerEnvResponse:
    save_worker_env(
        db,
        {
            "data_roots": body.data_roots,
            "default_input_dir": body.default_input_dir,
            "default_output_dir": body.default_output_dir,
        },
    )
    record_audit(
        db,
        user.id,
        "settings.update",
        {
            "data_roots": body.data_roots,
            "default_input_dir": body.default_input_dir,
            "default_output_dir": body.default_output_dir,
        },
    )
    return WorkerEnvResponse(**worker_env_payload(db, settings, can_edit=_can_edit(user)))
