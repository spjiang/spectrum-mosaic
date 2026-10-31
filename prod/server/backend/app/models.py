from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    roles: Mapped[list[Role]] = relationship(secondary="user_roles", back_populates="users")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    users: Mapped[list[User]] = relationship(secondary="user_roles", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class ParamDefinition(Base):
    __tablename__ = "param_definitions"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    stage_id: Mapped[str] = mapped_column(String(32))
    value_type: Mapped[str] = mapped_column(String(32))
    default_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    description: Mapped[str] = mapped_column(Text)
    advanced: Mapped[bool] = mapped_column(Boolean, default=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    required_when: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class ParamProfile(Base):
    __tablename__ = "param_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ParamProfileValue(Base):
    __tablename__ = "param_profile_values"
    profile_id: Mapped[int] = mapped_column(ForeignKey("param_profiles.id", ondelete="CASCADE"), primary_key=True)
    param_key: Mapped[str] = mapped_column(ForeignKey("param_definitions.key"), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, server_default=text("nextval('job_runs_seq')"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("param_profiles.id"), nullable=True)
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    run_attempt: Mapped[int] = mapped_column(Integer, default=1)
    params_snapshot: Mapped[dict] = mapped_column(JSONB)
    input_dir: Mapped[str] = mapped_column(Text)
    output_dir: Mapped[str] = mapped_column(Text)
    cache_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    products_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completed_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    global_percent: Mapped[float] = mapped_column(Float, default=0.0)
    stage_progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    eta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage_timings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 各节点功能步骤：名称、统计、耗时、状态。键是阶段 id。
    stage_steps: Mapped[dict] = mapped_column(JSONB, default=dict)
    n_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpu: Mapped[str | None] = mapped_column(Text, nullable=True)
    gpu_memory_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    native_gsd_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ortho_gsd_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobRunLog(Base):
    __tablename__ = "job_run_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_runs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB)


class IncrementalProject(Base):
    """增量测区。正式目录确定后不再改。一个测区只绑定一条拼图任务。"""

    __tablename__ = "incremental_projects"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    input_dir: Mapped[str] = mapped_column(Text, unique=True)
    inbox_dir: Mapped[str] = mapped_column(Text)
    output_root: Mapped[str] = mapped_column(Text)
    auto_run: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_minutes: Mapped[int] = mapped_column(Integer, default=10)
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=5)
    latest_shot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latest_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    current_output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class IncrementalFile(Base):
    """收件区里的一张航片。传完并入库后才进入正式目录。"""

    __tablename__ = "incremental_files"
    __table_args__ = (UniqueConstraint("project_id", "filename", name="uq_incremental_file"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incremental_projects.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(String(255))
    shot_index: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16))
    dataset_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class IncrementalRound(Base):
    """一轮增量拼图，对应一条现有处理任务。"""

    __tablename__ = "incremental_rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incremental_projects.id", ondelete="CASCADE")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    output_dir: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
