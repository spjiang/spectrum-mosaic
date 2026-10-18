from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth import ensure_role, hash_password
from app.config import Settings
from app.models import ParamDefinition, ParamProfileValue, SystemSetting, User
from app.param_catalog import PARAM_LABELS
from app.services.mq import check_rabbitmq, check_worker

log = logging.getLogger(__name__)


def _migration_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[1] / "db" / "migrations",
        here.parents[2] / "db" / "migrations",
        Path("/db/migrations"),
    ):
        if cand.is_dir():
            return cand
    return here.parents[2] / "db" / "migrations"


_REQUIRED_SCHEMA_SQL = """
ALTER TABLE param_definitions
  ADD COLUMN IF NOT EXISTS required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE param_definitions
  ADD COLUMN IF NOT EXISTS required_when JSONB;
UPDATE param_definitions
   SET required = (key IN ('input_dir', 'output_dir'));
UPDATE param_definitions
   SET required_when = CASE
     WHEN key = 'stop_after_stage' THEN '{"run_mode": "until_stage"}'::jsonb
     ELSE NULL
   END
"""


def _run_sql_script(db: Session, script: str) -> None:
    for stmt in script.split(";"):
        sql = stmt.strip()
        if sql:
            db.execute(text(sql))
    db.commit()


def _run_sql_file(db: Session, path: Path) -> None:
    _run_sql_script(db, path.read_text(encoding="utf-8"))


def _ensure_sql_named(db: Session, name: str) -> None:
    """按文件名跑一次迁移。文件不在镜像里就跳过，不挡启动。"""
    path = _migration_dir() / name
    if path.is_file():
        _run_sql_file(db, path)


def _ensure_param_schema(db: Session) -> None:
    """补 required / required_when 列，并同步必填标记。"""
    sql_path = _migration_dir() / "003_param_required.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    _run_sql_script(db, _REQUIRED_SCHEMA_SQL)


def _ensure_param_labels(db: Session) -> None:
    db.execute(text("ALTER TABLE param_definitions ADD COLUMN IF NOT EXISTS label VARCHAR(64)"))
    db.commit()
    n = 0
    for key, label in PARAM_LABELS.items():
        res = db.execute(
            text("UPDATE param_definitions SET label = :label WHERE key = :key AND (label IS DISTINCT FROM :label)"),
            {"key": key, "label": label},
        )
        n += res.rowcount or 0
    db.commit()
    if n:
        log.info("synced %s param labels", n)


_JOB_RUN_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS job_run_logs (
  id BIGSERIAL PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  level VARCHAR(16) NOT NULL DEFAULT 'info',
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_run_logs_job_id_id ON job_run_logs (job_id, id)
"""


def _ensure_job_run_logs(db: Session) -> None:
    sql_path = _migration_dir() / "005_job_run_logs.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    _run_sql_script(db, _JOB_RUN_LOGS_SQL)


def _ensure_param_catalog(db: Session) -> None:
    """空库才灌参数字典；已有行不改。方案模板不在这里写。"""
    n = db.scalar(select(func.count()).select_from(ParamDefinition)) or 0
    if n:
        return
    sql_path = _migration_dir() / "002_param_definitions.sql"
    if not sql_path.is_file():
        log.warning("missing %s", sql_path)
        return
    db.execute(text(sql_path.read_text(encoding="utf-8")))
    db.commit()


def _ensure_memory_gb_param(db: Session) -> None:
    sql_path = _migration_dir() / "006_memory_gb.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    db.execute(
        text(
            """
            INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
            VALUES ('memory_gb', 'S0_io', 'float', '0'::jsonb, '本任务进程内存预算（GB）。0 表示按容器当前可用内存自动下调并行度。', false, 25, false, '任务内存（GB）')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    db.commit()


def _ensure_job_memory_gb(db: Session) -> None:
    sql_path = _migration_dir() / "007_job_memory_gb.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    db.execute(text("ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS memory_gb DOUBLE PRECISION"))
    db.commit()


def _ensure_cpus_param(db: Session) -> None:
    sql_path = _migration_dir() / "009_cpus.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    db.execute(
        text(
            """
            INSERT INTO param_definitions (key, stage_id, value_type, default_value, description, advanced, sort_order, required, label)
            VALUES ('cpus', 'S0_io', 'int', '0'::jsonb, '本任务可用 CPU 核数。0 表示按 Docker 引擎 CPU 自动封顶。', false, 26, false, '任务 CPU（核）')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    db.commit()


def _ensure_job_cpus(db: Session) -> None:
    sql_path = _migration_dir() / "010_job_cpus.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    db.execute(text("ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS cpus INTEGER"))
    db.commit()


def _ensure_job_seq(db: Session) -> None:
    """任务对外展示号：从 1 递增。UUID 仍作主键，不改 Worker 投递。"""
    sql_path = _migration_dir() / "008_job_seq.sql"
    if sql_path.is_file():
        _run_sql_file(db, sql_path)
        return
    db.execute(text("CREATE SEQUENCE IF NOT EXISTS job_runs_seq"))
    db.execute(text("ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS seq BIGINT"))
    db.execute(
        text(
            """
            WITH numbered AS (
              SELECT id, ROW_NUMBER() OVER (ORDER BY created_at ASC, id ASC) AS n
              FROM job_runs
              WHERE seq IS NULL
            )
            UPDATE job_runs AS j SET seq = numbered.n FROM numbered WHERE j.id = numbered.id
            """
        )
    )
    db.execute(
        text(
            """
            SELECT setval(
              'job_runs_seq',
              GREATEST(COALESCE((SELECT MAX(seq) FROM job_runs), 1), 1),
              (SELECT MAX(seq) FROM job_runs) IS NOT NULL
            )
            """
        )
    )
    db.execute(text("ALTER TABLE job_runs ALTER COLUMN seq SET DEFAULT nextval('job_runs_seq')"))
    db.execute(text("ALTER TABLE job_runs ALTER COLUMN seq SET NOT NULL"))
    db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS job_runs_seq_uidx ON job_runs (seq)"))
    db.execute(text("ALTER SEQUENCE job_runs_seq OWNED BY job_runs.seq"))
    db.commit()


_OLD_SOURCE = "/prod/source"
_NEW_WORKER = "/prod/server/worker"


def _rewrite_source_paths(obj: object) -> object:
    if isinstance(obj, str):
        return obj.replace(_OLD_SOURCE, _NEW_WORKER)
    if isinstance(obj, list):
        return [_rewrite_source_paths(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_source_paths(v) for k, v in obj.items()}
    return obj


_INPUT_SURVEY = "/data/input/MAX_20251017"
_INPUT_SHOT = "/data/input/MAX_20251017/MAX_20251017_001"


def _to_container_data_path(key: str, value: object) -> object:
    """方案里的宿主机路径改成容器约定：/data/input/<测区>/<架次>、/data/output/runs/<任务>。"""
    if not isinstance(value, str) or not value:
        return value
    if key in {"input_dir", "default_input_dir"}:
        text = value.rstrip("/")
        if text == _INPUT_SURVEY or (
            "MAX_20251017" in text and "拼图结果" not in text and "MAX_20251017_001" not in text
        ):
            return _INPUT_SHOT
        if "MAX_20251017_001" in text and not text.startswith("/data/"):
            return _INPUT_SHOT
    if value.startswith("/data/"):
        return value
    if key in {"output_dir", "default_output_dir"}:
        for marker in ("/server/worker/runs/", "/prod/source/runs/", "/source/runs/"):
            if marker in value:
                name = value.split(marker, 1)[-1].strip("/")
                if "/" in name:
                    name = name.split("/")[0]
                return f"/data/output/runs/{name}" if name else "/data/output/runs"
        if value.startswith("/") and "runs" in value:
            return "/data/output/runs"
    if key in {"cache_dir", "reuse_dsm", "benchmark_dir", "grid_reference"} and not value.startswith("/data/"):
        return None
    if key in {"data_roots"} and not value.startswith("/data"):
        return "/data"
    return value


def _migrate_legacy_source_paths(db: Session) -> None:
    """旧路径迁到 server/worker，再收成 /data 约定。"""
    n = 0
    for row in db.scalars(select(ParamDefinition)).all():
        new = _rewrite_source_paths(row.default_value)
        new = _to_container_data_path(row.key, new)
        if row.key == "output_dir" and isinstance(new, str) and new.startswith("/data/output/runs/"):
            new = "/data/output/runs"
        if new != row.default_value:
            row.default_value = new
            n += 1
    for row in db.scalars(select(ParamProfileValue)).all():
        new = _rewrite_source_paths(row.value)
        new = _to_container_data_path(row.param_key, new)
        if new != row.value:
            row.value = new
            n += 1
    stale_cli = db.get(SystemSetting, "cli")
    if stale_cli is not None:
        db.delete(stale_cli)
        n += 1
    for row in db.scalars(select(SystemSetting)).all():
        raw = _rewrite_source_paths(row.value)
        if isinstance(raw, dict):
            new = {k: _to_container_data_path(k, v) for k, v in raw.items()}
        else:
            new = raw
        if new != row.value:
            row.value = new
            n += 1
    if n:
        db.commit()
        log.info("migrated %s legacy paths → /data layout", n)


def bootstrap(db: Session, settings: Settings) -> None:
    t0 = time.perf_counter()
    for name in ("admin", "configurator", "executor", "viewer"):
        ensure_role(db, name)
    db.commit()
    _ensure_param_schema(db)
    _ensure_param_catalog(db)
    _ensure_param_schema(db)
    _ensure_memory_gb_param(db)
    _ensure_job_memory_gb(db)
    _ensure_cpus_param(db)
    _ensure_job_cpus(db)
    _ensure_job_seq(db)
    _ensure_param_labels(db)
    _ensure_job_run_logs(db)
    _ensure_sql_named(db, "011_terrain_band.sql")
    _ensure_sql_named(db, "012_grid_lock_and_radiometry.sql")
    _ensure_sql_named(db, "013_edge_trim.sql")
    _ensure_sql_named(db, "014_flatten_edge_and_profile.sql")
    _ensure_sql_named(db, "015_edge_trim_zero.sql")
    _ensure_sql_named(db, "016_job_stage_steps.sql")
    _ensure_sql_named(db, "017_gpu.sql")
    _ensure_sql_named(db, "018_product_options.sql")
    _migrate_legacy_source_paths(db)
    user = db.scalar(select(User).where(User.username == settings.bootstrap_admin_user))
    if user is None:
        admin = User(
            username=settings.bootstrap_admin_user,
            password_hash=hash_password(settings.bootstrap_admin_password),
            is_active=True,
        )
        admin.roles.append(ensure_role(db, "admin"))
        db.add(admin)
        db.commit()
    _ensure_demo_users(db)
    log.info("bootstrap finished in %.2fs", time.perf_counter() - t0)


def _ensure_demo_users(db: Session) -> None:
    """首次安装补齐示例角色账号，便于对照菜单权限。已存在则跳过。"""
    samples = (
        ("executor", "executor123", "executor"),
        ("configurator", "configurator123", "configurator"),
        ("viewer", "viewer123", "viewer"),
    )
    for username, password, role in samples:
        if db.scalar(select(User).where(User.username == username)) is not None:
            continue
        u = User(username=username, password_hash=hash_password(password), is_active=True)
        u.roles.append(ensure_role(db, role))
        db.add(u)
    db.commit()


def health_payload(db: Session, settings: Settings) -> dict:
    """轻量探活：只查 PostgreSQL，不做 RabbitMQ/CLI（那些只在健康检查页）。"""
    try:
        db.execute(text("SELECT 1"))
        pg = "ok"
    except Exception as exc:  # noqa: BLE001
        pg = f"error: {exc}"
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    status = "ok" if pg == "ok" else "degraded"
    return {"status": status, "postgres": pg, "rabbitmq": "skipped"}


def system_status_payload(db: Session, settings: Settings, *, can_edit: bool = False) -> dict:
    """健康检查：只看服务是否在。Worker 已监听 mosaic.jobs 则 MQ 也视为可用。"""
    try:
        db.execute(text("SELECT 1"))
        pg = "ok"
        db.commit()
    except Exception as exc:  # noqa: BLE001
        pg = f"error: {exc}"
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    try:
        db.close()
    except Exception:  # noqa: BLE001
        pass

    try:
        worker = check_worker(settings.rabbitmq_url)
    except Exception as exc:  # noqa: BLE001
        worker = f"error: {exc}"

    if worker == "ok":
        rq = "ok"
    else:
        try:
            rq = check_rabbitmq(settings.rabbitmq_url)
        except Exception as exc:  # noqa: BLE001
            rq = f"error: {exc}"

    status = "ok" if pg == "ok" and rq == "ok" and worker == "ok" else "degraded"
    return {
        "status": status,
        "postgres": pg,
        "rabbitmq": rq,
        "worker": worker,
        "can_edit": can_edit,
    }


def worker_env_payload(db: Session, settings: Settings, *, can_edit: bool = False) -> dict:
    from app.services.worker_env import worker_env_view

    view = worker_env_view(db, settings)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"worker_env": view, "can_edit": can_edit}
