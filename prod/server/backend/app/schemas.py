from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    roles: list[str]


class UserOut(BaseModel):
    id: int
    username: str
    is_active: bool
    roles: list[str]
    created_at: datetime | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    roles: list[str] = Field(default_factory=lambda: ["viewer"])


class UserUpdate(BaseModel):
    password: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None


class LoginForm(BaseModel):
    username: str
    password: str


class ParamDefOut(BaseModel):
    key: str
    stage_id: str
    value_type: str
    default_value: Any
    description: str
    advanced: bool
    required: bool = False
    required_when: dict[str, Any] | None = None
    label: str | None = None
    sort_order: int

    class Config:
        from_attributes = True


class ProfileCreate(BaseModel):
    name: str
    description: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    preset: str | None = None


class ProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    values: dict[str, Any] | None = None
    preset: str | None = None


class ProfileOut(BaseModel):
    id: int
    name: str
    description: str
    version: int
    preset: str | None
    values: dict[str, Any]
    updated_at: datetime

    class Config:
        from_attributes = True


class JobCreate(BaseModel):
    profile_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    input_dir: str
    output_dir: str
    title: str = Field(default="", max_length=80)


class JobContinue(BaseModel):
    stop_after_stage: str | None = None


class EvaluateIn(BaseModel):
    n: int = 20


class FileTicketIn(BaseModel):
    kind: str
    name: str | None = None


class EvaluateClause(BaseModel):
    no: str
    text: str
    result: str
    detail: str


class AccuracyPoint(BaseModel):
    name: str
    col: float
    row: float
    east: float | None = None
    north: float | None = None
    lon: float | None = None
    lat: float | None = None
    gps_x: float | None = None
    gps_y: float | None = None
    gps_z: float | None = None
    gps_col: float | None = None
    gps_row: float | None = None
    dist_m: float | None = None
    err_px: float | None = None


class AccuracyBand(BaseModel):
    id: str
    label: str
    reference: bool = False
    gsd_m: float | None = None


class AccuracyState(BaseModel):
    min_count: int
    max_count: int
    max_mean_px: float
    crs: str = "EPSG:4326"
    reference_band: str = ""
    ortho_epsg: str = ""
    gsd_m: float | None = None
    evaluated: bool = False
    mean_px: float | None = None
    max_px: float | None = None
    points: list[AccuracyPoint] = Field(default_factory=list)
    bands: list[AccuracyBand] = Field(default_factory=list)
    accepted: bool
    message: str
    clauses: list[EvaluateClause] = Field(default_factory=list)


class AccuracyConfigIn(BaseModel):
    min_count: int = 20
    max_count: int = 30
    max_mean_px: float = 2.0
    reference_band: str | None = None


class AccuracyPickIn(BaseModel):
    col: float
    row: float
    name: str | None = None


class AccuracyGpsIn(BaseModel):
    x: float | None = None
    y: float | None = None
    z: float | None = None
    crs: str | None = None


class SeamRegionIn(BaseModel):
    image_id: int
    points: list[list[float]]


class SeamPreferIn(BaseModel):
    prefer_ids: str = ""
    regions: list[SeamRegionIn] = Field(default_factory=list)
    clear_edits: bool = False


class SeamBranchIn(BaseModel):
    version: str = "current"


class SeamTaskCreate(BaseModel):
    source_job_id: UUID


class SeamPointsIn(BaseModel):
    points: list[list[float]] = Field(default_factory=list)


class JobsClearedOut(BaseModel):
    deleted: int


class JobListOut(BaseModel):
    """列表/轮询用：只读库里的状态字段，不含配置快照。"""

    id: UUID
    seq: int
    title: str = ""
    status: str
    run_attempt: int
    profile_id: int | None = None
    profile_version: int | None = None
    input_dir: str
    output_dir: str
    cache_dir: str | None = None
    log_dir: str | None = None
    process_dir: str | None = None
    products_dir: str | None = None
    report_pdf_path: str | None = None
    current_stage: str | None = None
    completed_stage: str | None = None
    global_percent: float
    stage_progress: float
    message: str
    eta_seconds: int | None = None
    error_summary: str | None = None
    stage_timings: dict[str, Any] = Field(default_factory=dict)
    stage_steps: dict[str, Any] = Field(default_factory=dict)
    n_shots: int | None = None
    memory_gb: float | None = None
    cpus: int | None = None
    gpu: str | None = None
    gpu_memory_gb: float | None = None
    native_gsd_m: float | None = None
    ortho_gsd_m: float | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    has_rgb: bool = False

    class Config:
        from_attributes = True


class JobOut(JobListOut):
    params_snapshot: dict[str, Any] = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str
    postgres: str
    rabbitmq: str


class WorkerEnvOut(BaseModel):
    rabbitmq_url: str
    pythonpath: str = "/app"
    workdir: str = "/app"
    mplbackend: str = "Agg"
    data_roots: str = ""
    default_input_dir: str = ""
    default_output_dir: str = ""
    source: str = "env"


class WorkerEnvUpdate(BaseModel):
    data_roots: str = ""
    default_input_dir: str = ""
    default_output_dir: str = ""


class SystemStatusOut(BaseModel):
    status: str
    postgres: str
    rabbitmq: str
    worker: str = "unknown"
    can_edit: bool = False


class WorkerInspectOut(BaseModel):
    status: str
    alive: bool
    heartbeat_age_seconds: float | None = None
    heartbeat_path: str
    queue: str
    mq_status: str
    consumers: int | None = None
    queued_messages: int | None = None
    active_jobs: list[dict[str, Any]] = Field(default_factory=list)
    worker: dict[str, Any] | None = None
    engine_cpu: int | None = None


class WorkerKillOut(BaseModel):
    killed: int
