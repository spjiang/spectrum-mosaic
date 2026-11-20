"""推扫拼图任务：建任务、投递推扫 worker、取消重跑、接收进度与事件、列成果。"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomJob, PushbroomJobLog, User
from app.services.paths import prepare_job_dirs, stamp_run_output_dir, trial_dirs, validate_paths
from app.services.pushbroom_calibration import latest_calibration, with_active_calibration
from app.services.pushbroom_check import quick_check

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE = frozenset({"queued", "running", "cancelling"})
RETRYABLE = frozenset({"failed", "cancelled"})
# 成果下载只开放这两个子目录。process/ 里是中间件，log/ 走日志接口。
DOWNLOAD_DIRS = ("products", "report")
LOG_TAIL = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def roots_for(user: User, settings: Settings, *, admin: bool) -> tuple[str, str]:
    """管理员用推扫公共目录。其它账号沿用框幅的约定，只能用自己名下的目录。"""
    if admin:
        return settings.pushbroom_input_root, settings.pushbroom_output_root
    in_root, out_root = trial_dirs(user.username)
    return in_root, f"{out_root}/pushbroom"


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def assert_input_allowed(input_dir: str, input_root: str, settings: Settings) -> Path:
    text = (input_dir or "").strip()
    if not text:
        raise HTTPException(400, "请选择架次目录")
    if ".." in Path(text).parts:
        raise HTTPException(400, "架次目录不能包含 ..")
    inp = Path(text).expanduser().resolve()
    if not _inside(inp, Path(input_root).resolve()):
        raise HTTPException(400, f"架次目录只能使用 {input_root} 及其子目录")
    roots = [Path(r).expanduser().resolve() for r in settings.data_root_list()]
    if roots and not any(_inside(inp, r) for r in roots):
        raise HTTPException(400, f"路径必须位于 DATA_ROOTS 下: {settings.data_roots}")
    return inp


def append_log(db: Session, job_id: uuid.UUID, message: str, *, level: str = "info", commit: bool = True) -> None:
    db.add(PushbroomJobLog(job_id=job_id, message=message, level=level, created_at=_utcnow()))
    if commit:
        db.commit()


def _control_path(job: PushbroomJob) -> Path:
    return Path(job.output_dir) / "log" / "control.json"


def _write_control(job: PushbroomJob, *, cancel: bool) -> None:
    """worker 开跑前和运行中都会读这个文件。消息丢了也能停。"""
    path = _control_path(job)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cancel": cancel, "run_attempt": job.run_attempt}),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("write control %s failed: %s", path, exc)


def job_message(job: PushbroomJob, username: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": str(job.id),
        "run_attempt": job.run_attempt,
        "input_dir": job.input_dir,
        "output_dir": job.output_dir,
        "params": dict(job.params or {}),
        "submitted_by": username,
    }


def _commit_and_publish(db: Session, mq, job: PushbroomJob, username: str) -> PushbroomJob:
    """先落库再投递：worker 回报的进度总能找到记录。投递失败就把这条记成失败。"""
    db.commit()
    try:
        mq.publish_pushbroom_job(job_message(job, username))
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.message = "失败"
        job.error_summary = f"消息队列不可用，任务未投递：{exc}"
        job.finished_at = _utcnow()
        append_log(db, job.id, job.error_summary, level="error", commit=False)
        db.commit()
        raise HTTPException(503, job.error_summary) from exc
    db.refresh(job)
    return job


def create_job(
    db: Session,
    settings: Settings,
    mq,
    *,
    user: User,
    admin: bool,
    input_dir: str,
    title: str,
    params: dict[str, Any],
) -> PushbroomJob:
    input_root, output_root = roots_for(user, settings, admin=admin)
    inp = assert_input_allowed(input_dir, input_root, settings)
    check = quick_check(inp)
    if not check.ok:
        raise HTTPException(400, {"message": "架次核对未通过", "check": check.as_dict()})
    total_bands = int(check.flight.get("bands") or 0)
    bad = [b for b in params.get("bands") or [] if b > total_bands]
    if bad:
        raise HTTPException(400, f"波段序号超出范围（共 {total_bands} 个波段）：{bad}")
    params = with_active_calibration(params, latest_calibration(db))
    out = stamp_run_output_dir(str(Path(output_root) / inp.name))
    validate_paths(settings, str(inp), out)
    job = PushbroomJob(
        id=uuid.uuid4(),
        title=(title or "").strip() or inp.name,
        created_by=user.id,
        status="queued",
        run_attempt=1,
        input_dir=str(inp),
        output_dir=out,
        params=params,
        flight=check.flight,
        message="排队中",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    prepare_job_dirs(out)
    db.add(job)
    db.flush()
    for warn in check.warnings:
        append_log(db, job.id, warn, level="warning", commit=False)
    append_log(db, job.id, f"已提交：{inp}", commit=False)
    _write_control(job, cancel=False)
    return _commit_and_publish(db, mq, job, user.username)


def calibration_warning(output_dir: str) -> str | None:
    """成果报告里的标定不符提示。没有报告时返回空。"""
    path = Path(output_dir) / "report" / "report.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for item in data.get("warnings") or []:
        text = str(item)
        if "传感器标定" in text:
            return text
    return None


def get_job(db: Session, job_id: uuid.UUID) -> PushbroomJob:
    job = db.get(PushbroomJob, job_id)
    if job is None:
        raise HTTPException(404, "推扫任务不存在")
    return job


def page_jobs(
    db: Session,
    page: int,
    page_size: int,
    *,
    owner_id: int | None,
    status: str | None = None,
) -> tuple[list[PushbroomJob], int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = select(PushbroomJob)
    cq = select(func.count()).select_from(PushbroomJob)
    if owner_id is not None:
        q = q.where(PushbroomJob.created_by == owner_id)
        cq = cq.where(PushbroomJob.created_by == owner_id)
    if status:
        q = q.where(PushbroomJob.status == status)
        cq = cq.where(PushbroomJob.status == status)
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(q.order_by(PushbroomJob.seq.desc()).offset((page - 1) * page_size).limit(page_size)))
    return rows, total


def cancel_job(db: Session, mq, job: PushbroomJob, *, worker_online: bool) -> PushbroomJob:
    if job.status in TERMINAL:
        raise HTTPException(409, f"任务已结束（{job.status}），无需取消")
    _write_control(job, cancel=True)
    if job.status == "queued" or not worker_online:
        # 还没开跑，或者 worker 已经不在：直接收口，worker 之后取到这条也会按控制文件跳过
        job.status = "cancelled"
        job.message = "已取消"
        job.finished_at = _utcnow()
    else:
        job.status = "cancelling"
        job.message = "正在取消"
    job.updated_at = _utcnow()
    append_log(db, job.id, "用户取消", commit=False)
    try:
        mq.publish_pushbroom_control(
            {"schema_version": SCHEMA_VERSION, "job_id": str(job.id), "run_attempt": job.run_attempt, "action": "cancel"}
        )
    except Exception as exc:  # noqa: BLE001
        append_log(db, job.id, f"取消消息未发出，worker 会按控制文件停止：{exc}", level="warning", commit=False)
    db.commit()
    db.refresh(job)
    return job


def retry_job(db: Session, mq, job: PushbroomJob, *, username: str) -> PushbroomJob:
    if job.status not in RETRYABLE:
        raise HTTPException(409, f"当前状态不能重跑：{job.status}")
    check = quick_check(job.input_dir)
    if not check.ok:
        raise HTTPException(400, {"message": "架次核对未通过", "check": check.as_dict()})
    # 每次重跑换一个日期目录，不和上一次的半成品混在一起
    out = stamp_run_output_dir(str(Path(job.output_dir).parent))
    job.output_dir = out
    job.run_attempt += 1
    job.status = "queued"
    job.current_stage = None
    job.stage_progress = 0.0
    job.global_percent = 0.0
    job.message = "排队中"
    job.error_summary = None
    job.stage_timings = {}
    job.products = []
    job.flight = check.flight
    job.started_at = None
    job.finished_at = None
    job.updated_at = _utcnow()
    prepare_job_dirs(out)
    append_log(db, job.id, f"第 {job.run_attempt} 次运行，输出到 {out}", commit=False)
    _write_control(job, cancel=False)
    return _commit_and_publish(db, mq, job, username)


def delete_job(db: Session, job: PushbroomJob, *, settings: Settings, delete_files: bool) -> None:
    if job.status in ACTIVE:
        raise HTTPException(409, "任务未结束，请先取消")
    if delete_files:
        _remove_output(job, settings)
    db.delete(job)
    db.commit()


def _remove_output(job: PushbroomJob, settings: Settings) -> None:
    out = Path(job.output_dir).resolve()
    allowed = [Path(settings.pushbroom_output_root).resolve(), Path("/data/output/runs").resolve()]
    if not any(root in out.parents for root in allowed):
        raise HTTPException(400, f"成果目录不在推扫输出目录下，未删除：{out}")
    if out.is_dir():
        shutil.rmtree(out, ignore_errors=False)


def _is_current(job: PushbroomJob, payload: dict[str, Any]) -> bool:
    try:
        attempt = int(payload.get("run_attempt") or 0)
    except (TypeError, ValueError):
        return False
    return attempt == job.run_attempt


def _load_for_update(db: Session, payload: dict[str, Any]) -> PushbroomJob | None:
    raw = payload.get("job_id")
    try:
        job_id = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        log.warning("pushbroom message without valid job_id: %r", raw)
        return None
    job = db.get(PushbroomJob, job_id)
    if job is None or not _is_current(job, payload):
        return None
    return job


def apply_progress(db: Session, payload: dict[str, Any]) -> PushbroomJob | None:
    job = _load_for_update(db, payload)
    if job is None or job.status in TERMINAL:
        return None
    if job.status == "queued":
        job.status = "running"
        job.started_at = job.started_at or _utcnow()
    stage = payload.get("stage_id")
    if stage:
        job.current_stage = str(stage)[:32]
    for key in ("stage_progress", "global_percent"):
        if payload.get(key) is not None:
            try:
                setattr(job, key, max(0.0, min(100.0, float(payload[key]))))
            except (TypeError, ValueError):
                pass
    if payload.get("message"):
        job.message = str(payload["message"])
    job.updated_at = _utcnow()
    db.commit()
    return job


def apply_event(db: Session, payload: dict[str, Any]) -> PushbroomJob | None:
    job = _load_for_update(db, payload)
    if job is None:
        return None
    event = str(payload.get("event") or "")
    now = _utcnow()
    msg = str(payload.get("message") or "")
    if event == "log":
        append_log(db, job.id, msg, level=str(payload.get("level") or "info"))
        return job
    if job.status in TERMINAL:
        return None
    if event == "started":
        if job.status == "cancelling":
            return job
        job.status = "running"
        job.started_at = job.started_at or now
        job.message = msg or "运行中"
        append_log(db, job.id, msg or "worker 已开始", commit=False)
    elif event == "stage_done":
        stage = str(payload.get("stage_id") or "")
        timings = dict(job.stage_timings or {})
        if stage:
            timings[stage] = payload.get("elapsed_s")
        job.stage_timings = timings
        append_log(db, job.id, msg or f"{stage} 完成", commit=False)
    elif event == "succeeded":
        job.status = "succeeded"
        job.global_percent = 100.0
        job.stage_progress = 100.0
        job.products = list(payload.get("products") or [])
        job.message = msg or "完成"
        job.finished_at = now
        append_log(db, job.id, job.message, commit=False)
    elif event in {"failed", "rejected"}:
        job.status = "failed"
        job.error_summary = str(payload.get("error") or msg or "worker 报告失败")[:4000]
        job.message = "失败"
        job.finished_at = now
        append_log(db, job.id, job.error_summary, level="error", commit=False)
    elif event == "cancelled":
        job.status = "cancelled"
        job.message = "已取消"
        job.finished_at = now
        append_log(db, job.id, msg or "已取消", commit=False)
    else:
        log.warning("unknown pushbroom event %r for %s", event, job.id)
        return None
    job.updated_at = now
    db.commit()
    return job


def format_logs(db: Session, job: PushbroomJob, *, tail: int = LOG_TAIL) -> str:
    rows = list(
        db.scalars(
            select(PushbroomJobLog).where(PushbroomJobLog.job_id == job.id).order_by(PushbroomJobLog.id.asc())
        )
    )
    lines = [
        f"{(r.created_at or _utcnow()).astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} [{r.level}] {r.message}"
        for r in rows
    ]
    run_log = Path(job.output_dir) / "log" / "run.log"
    if run_log.is_file():
        try:
            lines.extend(run_log.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            pass
    if not lines:
        return "(暂无日志)"
    return "\n".join(lines[-tail:])


def list_files(job: PushbroomJob) -> list[dict[str, Any]]:
    base = Path(job.output_dir)
    out: list[dict[str, Any]] = []
    for sub in DOWNLOAD_DIRS:
        d = base / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            st = p.stat()
            out.append(
                {
                    "path": f"{sub}/{p.name}",
                    "name": p.name,
                    "kind": sub,
                    "size": st.st_size,
                    "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                }
            )
    return out


def resolve_file(job: PushbroomJob, rel: str) -> Path:
    parts = Path(rel or "").parts
    if len(parts) != 2 or parts[0] not in DOWNLOAD_DIRS or parts[1] in {"..", "."}:
        raise HTTPException(404, "文件不存在")
    path = (Path(job.output_dir) / parts[0] / parts[1]).resolve()
    if path.parent != (Path(job.output_dir) / parts[0]).resolve() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return path
