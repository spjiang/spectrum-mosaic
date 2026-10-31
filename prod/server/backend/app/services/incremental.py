"""增量收片。收件区按间隔扫描，传完的文件立刻进入正式目录。

拼图只绑定这一条任务：没有就创建，已有就在原任务上从头再跑。
不另建任务记录，也不改空三和正射算法。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import IncrementalFile, IncrementalProject, IncrementalRound, JobRun
from app.services.jobs import append_run_log, assert_single_running, create_job, resolve_paths
from app.services.mq import MQPublisher
from app.services.paths import detach_derived_dirs, stamp_run_output_dir, validate_paths

log = logging.getLogger("uvicorn.error")

_NAME = re.compile(
    r"^MAX_(\d+)_(Color|(?P<wl>\d+)nm)_(?P<role>[DWRdw])\.(?:jpg|jpeg|tif|tiff)$",
    re.IGNORECASE,
)
BUSY = ("queued", "running", "paused", "awaiting_continue")


def parse_capture_name(name: str) -> tuple[int, str, str] | None:
    """文件名 → 曝光号、波段、角色。对不上命名的文件不入册。"""
    match = _NAME.match(Path(name).name)
    if match is None:
        return None
    role = match.group("role").upper()
    wl = match.group("wl")
    band = f"{int(wl)}nm" if wl else "Color"
    return int(match.group(1)), band, role


def next_arrival(previous: tuple[str, int] | None, size: int) -> str:
    """同一文件连续两次大小不变，才算传完。"""
    if size <= 0 or previous is None:
        return "incoming"
    status, prev_size = previous
    if status == "ingested":
        return "ingested"
    if prev_size == size and status in {"incoming", "ready"}:
        return "ready"
    return "incoming"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def latest_marker(rows: list[tuple[int, str, datetime]]) -> tuple[int, str, datetime] | None:
    """已入库记录里曝光号最大的一张。同一曝光号保留最后入库的文件名。"""
    if not rows:
        return None
    top = max(row[0] for row in rows)
    pool = [row for row in rows if row[0] == top]
    return max(pool, key=lambda row: _aware(row[2]))


def files_newer_than(started_at: datetime | None, copied_at: list[datetime]) -> bool:
    """这些入库时间里，是否有比任务开始更晚的。"""
    if not copied_at:
        return False
    if started_at is None:
        return True
    started = _aware(started_at)
    return any(_aware(item) > started for item in copied_at)


def mosaic_decision(
    job_status: str | None,
    job_started_at: datetime | None,
    copied_at: list[datetime],
    *,
    manual: bool,
) -> str:
    """create 新建唯一任务，rerun 在原任务上重跑，wait 任务还在忙，skip 这次不拼。"""
    if job_status in BUSY:
        return "wait"
    if job_status is None:
        if manual or copied_at:
            return "create"
        return "skip"
    if manual or files_newer_than(job_started_at, copied_at):
        return "rerun"
    return "skip"


def inbox_dir_for(input_dir: str) -> str:
    """收件区放在正式目录旁边，扫描正式目录时看不到未传完的文件。"""
    path = Path(input_dir).expanduser().resolve()
    return str(path.with_name(path.name + ".inbox"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _under_roots(settings: Settings, path: Path) -> None:
    roots = [Path(r).expanduser().resolve() for r in settings.data_root_list()]
    if roots and not any(path == root or root in path.parents for root in roots):
        raise HTTPException(400, f"路径必须位于 DATA_ROOTS 下: {settings.data_roots}")


def create_project(
    db: Session,
    settings: Settings,
    *,
    name: str,
    input_dir: str,
    output_root: str,
    user_id: int | None,
    auto_run: bool = False,
    scan_interval_minutes: int = 5,
) -> IncrementalProject:
    title = name.strip()
    if not title:
        raise HTTPException(400, "请填写测区名称")
    if scan_interval_minutes < 1 or scan_interval_minutes > 24 * 60:
        raise HTTPException(400, "扫描间隔需在 1 到 1440 分钟之间")
    validate_paths(settings, input_dir, output_root)
    dataset = Path(input_dir).expanduser().resolve()
    out = Path(output_root).expanduser().resolve()
    inbox = Path(inbox_dir_for(str(dataset)))
    if dataset.name.endswith(".inbox"):
        raise HTTPException(400, "正式目录不要用 .inbox 结尾")
    _under_roots(settings, inbox)
    if db.scalar(select(IncrementalProject).where(IncrementalProject.input_dir == str(dataset))):
        raise HTTPException(400, "这个正式目录已经有增量测区")
    dataset.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    row = IncrementalProject(
        id=uuid.uuid4(),
        name=title,
        input_dir=str(dataset),
        inbox_dir=str(inbox),
        output_root=str(out),
        auto_run=bool(auto_run),
        quiet_minutes=10,
        scan_interval_minutes=int(scan_interval_minutes),
        created_by=user_id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_projects(db: Session) -> list[IncrementalProject]:
    return list(db.scalars(select(IncrementalProject).order_by(IncrementalProject.created_at.desc())).all())


def page_projects(db: Session, page: int, page_size: int) -> tuple[list[IncrementalProject], int]:
    """测区管理列表。按创建时间倒序分页。"""
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(400, "分页参数不正确")
    total = int(db.scalar(select(func.count()).select_from(IncrementalProject)) or 0)
    rows = list(
        db.scalars(
            select(IncrementalProject)
            .order_by(IncrementalProject.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return rows, total


def get_project(db: Session, project_id: uuid.UUID) -> IncrementalProject:
    row = db.get(IncrementalProject, project_id)
    if row is None:
        raise HTTPException(404, "增量测区不存在")
    return row


def update_project(
    db: Session,
    project: IncrementalProject,
    *,
    name: str | None,
    auto_run: bool | None,
    scan_interval_minutes: int | None,
) -> IncrementalProject:
    if name is not None:
        title = name.strip()
        if not title:
            raise HTTPException(400, "请填写测区名称")
        project.name = title
    if auto_run is not None:
        project.auto_run = bool(auto_run)
    if scan_interval_minutes is not None:
        if scan_interval_minutes < 1 or scan_interval_minutes > 24 * 60:
            raise HTTPException(400, "扫描间隔需在 1 到 1440 分钟之间")
        project.scan_interval_minutes = int(scan_interval_minutes)
    project.updated_at = _now()
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: IncrementalProject) -> None:
    """删测区记录。正式目录、收件区和拼图成果都留在磁盘上。任务还在跑时不允许删。"""
    job = db.get(JobRun, project.current_job_id) if project.current_job_id else None
    if job is not None and job.status in BUSY:
        raise HTTPException(409, "这条测区的拼图任务还在进行，结束后再删除")
    db.delete(project)
    db.commit()


def _files(db: Session, project_id: uuid.UUID) -> list[IncrementalFile]:
    return list(
        db.scalars(
            select(IncrementalFile)
            .where(IncrementalFile.project_id == project_id)
            .order_by(IncrementalFile.shot_index, IncrementalFile.band)
        ).all()
    )


def _adopt_dataset(db: Session, project: IncrementalProject, known: dict[str, IncrementalFile]) -> None:
    """正式目录里已经有的航片记为已入库，避免下一轮再拷一遍。"""
    root = Path(project.input_dir)
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_file():
            continue
        parsed = parse_capture_name(path.name)
        if parsed is None or path.name in known:
            continue
        shot, band, role = parsed
        row = IncrementalFile(
            project_id=project.id,
            filename=path.name,
            shot_index=shot,
            band=band,
            role=role,
            size_bytes=path.stat().st_size,
            status="ingested",
            dataset_path=str(path),
            updated_at=_now(),
        )
        db.add(row)
        known[path.name] = row


def scan_inbox(db: Session, project: IncrementalProject) -> dict[str, int]:
    """看收件区。只更新记录，不在有任务时搬进正式目录。"""
    known = {row.filename: row for row in _files(db, project.id)}
    _adopt_dataset(db, project, known)
    inbox = Path(project.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    incoming = ready = skipped = 0
    for path in inbox.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        parsed = parse_capture_name(path.name)
        if parsed is None:
            skipped += 1
            continue
        seen.add(path.name)
        size = path.stat().st_size
        shot, band, role = parsed
        row = known.get(path.name)
        previous = (row.status, int(row.size_bytes)) if row is not None else None
        status = next_arrival(previous, size)
        if row is None:
            row = IncrementalFile(
                project_id=project.id,
                filename=path.name,
                shot_index=shot,
                band=band,
                role=role,
                size_bytes=size,
                status=status,
                updated_at=_now(),
            )
            db.add(row)
            known[path.name] = row
        elif row.status != status or int(row.size_bytes) != size:
            row.shot_index = shot
            row.band = band
            row.role = role
            row.size_bytes = size
            row.status = status
            row.updated_at = _now()
        if status == "incoming":
            incoming += 1
        elif status == "ready":
            ready += 1
    project.updated_at = _now()
    db.commit()
    return {"incoming": incoming, "ready": ready, "skipped": skipped, "seen": len(seen)}


def _copy_into_dataset(src: Path, dest: Path) -> str:
    """拷进正式目录。同名且大小不同则不覆盖。"""
    if dest.is_file():
        if dest.stat().st_size == src.stat().st_size:
            return "same"
        return "conflict"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.copying")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return "copied"


def ingest_ready(db: Session, project: IncrementalProject) -> dict[str, list[str]]:
    """把已经传完的文件拷进正式目录。任务还在跑时也入库，这一轮看不见的留给同一条任务的下一次。"""
    copied: list[str] = []
    same: list[str] = []
    conflict: list[str] = []
    for row in _files(db, project.id):
        if row.status != "ready":
            continue
        src = Path(project.inbox_dir) / row.filename
        if not src.is_file():
            continue
        dest = Path(project.input_dir) / row.filename
        outcome = _copy_into_dataset(src, dest)
        if outcome == "conflict":
            conflict.append(row.filename)
            continue
        row.status = "ingested"
        row.dataset_path = str(dest)
        row.size_bytes = dest.stat().st_size
        row.updated_at = _now()
        if outcome == "copied":
            copied.append(row.filename)
            src.unlink()
        else:
            same.append(row.filename)
            src.unlink()
    if copied or same:
        project.updated_at = _now()
    db.commit()
    return {"copied": copied, "same": same, "conflict": conflict}


def _remember_latest(db: Session, project: IncrementalProject) -> None:
    """把正式目录里最大的曝光号写回测区。"""
    rows = [
        (row.shot_index, row.filename, row.updated_at)
        for row in _files(db, project.id)
        if row.status == "ingested"
    ]
    marker = latest_marker(rows)
    if marker is None:
        return
    project.latest_shot_index = marker[0]
    project.latest_filename = marker[1]
    project.latest_ingested_at = marker[2]


def sweep(db: Session, project: IncrementalProject) -> dict:
    """扫描收件区，并把已经传完的文件拷进正式目录。"""
    stats = scan_inbox(db, project)
    moved = ingest_ready(db, project)
    _remember_latest(db, project)
    project.last_scan_at = _now()
    project.updated_at = _now()
    db.commit()
    return {
        "incoming": stats["incoming"],
        "ready": stats["ready"],
        "skipped": stats["skipped"],
        "seen": stats["seen"],
        "copied": moved["copied"],
        "conflict": moved["conflict"],
    }


def _dataset_has_image(project: IncrementalProject) -> bool:
    dataset = Path(project.input_dir)
    if not dataset.is_dir():
        return False
    return any(parse_capture_name(path.name) for path in dataset.iterdir() if path.is_file())


def _promote(db: Session, project: IncrementalProject) -> None:
    """当前成果只跟着这一条任务的成功结果走。重跑还没成功时，保留上一份成果路径。"""
    if project.current_job_id is None:
        found = None
        rounds = db.scalars(
            select(IncrementalRound)
            .where(IncrementalRound.project_id == project.id)
            .order_by(IncrementalRound.id.asc())
        ).all()
        for row in rounds:
            if row.job_id is None:
                continue
            job = db.get(JobRun, row.job_id)
            if job is not None and job.status == "succeeded":
                found = job
        if found is None:
            return
        project.current_job_id = found.id
        project.current_output_dir = found.output_dir
        project.updated_at = _now()
        db.commit()
        return
    job = db.get(JobRun, project.current_job_id)
    if job is None or job.status != "succeeded" or not job.output_dir:
        return
    if project.current_output_dir == job.output_dir:
        return
    project.current_output_dir = job.output_dir
    project.updated_at = _now()
    db.commit()


def _other_running(db: Session, job_id: uuid.UUID | None) -> bool:
    running = db.scalar(select(JobRun).where(JobRun.status == "running"))
    return running is not None and running.id != job_id


_RESUME_KEYS = (
    "reuse_dsm",
    "start_stage",
    "stop_after_stage",
    "seam_prefer_ids",
    "seam_edit_path",
    "seam_cancel_token",
)


def _rerun_same_job(
    db: Session,
    settings: Settings,
    mq: MQPublisher,
    project: IncrementalProject,
    job: JobRun,
) -> JobRun:
    """在原来的任务上从头再拼。新开一个输出目录，避免沿用上一轮的空三缓存。"""
    output_dir = stamp_run_output_dir(project.output_root)
    validate_paths(settings, project.input_dir, output_dir)
    snap = dict(job.params_snapshot or {})
    for key in _RESUME_KEYS:
        snap.pop(key, None)
    snap["run_mode"] = "full"
    detach_derived_dirs(snap, project.output_root)
    paths = resolve_paths(snap, project.input_dir, output_dir)
    for key, value in paths.items():
        snap[key] = value
    job.params_snapshot = snap
    job.input_dir = paths["input_dir"]
    job.output_dir = paths["output_dir"]
    job.cache_dir = paths["cache_dir"]
    job.log_dir = paths["log_dir"]
    job.process_dir = paths["process_dir"]
    job.products_dir = paths["products_dir"]
    job.report_pdf_path = paths["report_pdf_path"]
    job.current_stage = None
    job.completed_stage = None
    job.global_percent = 0.0
    job.stage_progress = 0.0
    job.eta_seconds = None
    job.error_summary = None
    job.stage_timings = {}
    job.stage_steps = {}
    job.n_shots = None
    job.status = "running"
    job.run_attempt = int(job.run_attempt or 1) + 1
    job.started_at = _now()
    job.finished_at = None
    job.message = "正式目录有新航片，在同一条任务上重新拼"
    job.updated_at = _now()
    project.updated_at = _now()
    db.commit()
    append_run_log(db, job.id, f"增量拼图在同一条任务上重新开始 output={job.output_dir}")
    try:
        mq.publish_job(
            {
                "schema_version": 1,
                "job_id": str(job.id),
                "run_attempt": job.run_attempt,
                "params_snapshot": snap,
                "action": "start",
            }
        )
        append_run_log(db, job.id, "消息队列已投递 mosaic.jobs")
    except Exception as exc:  # noqa: BLE001
        append_run_log(db, job.id, f"消息队列投递失败: {exc}", level="error")
        job.status = "failed"
        job.finished_at = _now()
        job.message = f"队列投递失败: {exc}"
        job.updated_at = _now()
        db.commit()
    db.refresh(job)
    return job


def launch_mosaic(
    db: Session,
    settings: Settings,
    mq: MQPublisher,
    project: IncrementalProject,
    *,
    user_id: int | None,
    manual: bool,
) -> JobRun | None:
    """拼图只走测区上的这一条任务。忙着就等，不另建一条。"""
    files = _files(db, project.id)
    copied_at = [row.updated_at for row in files if row.status == "ingested"]
    job = db.get(JobRun, project.current_job_id) if project.current_job_id else None
    if project.current_job_id is not None and job is None:
        project.current_job_id = None
        db.commit()
    decision = mosaic_decision(
        job.status if job is not None else None,
        job.started_at if job is not None else None,
        copied_at,
        manual=manual,
    )
    if decision == "skip":
        return None
    if decision == "wait":
        if manual:
            raise HTTPException(409, "这条任务还在进行。新航片已经在正式目录里，结束后在同一条任务上再拼")
        return None
    if not _dataset_has_image(project):
        if manual:
            raise HTTPException(400, "正式目录里还没有航片。请先把文件传到收件区，等扫描入库后再拼")
        return None
    try:
        assert_single_running(db)
    except HTTPException:
        if manual:
            raise HTTPException(409, "已有其他拼图任务在进行，这条任务要等它结束后再开始")
        return None
    if decision == "create":
        job = create_job(
            db,
            settings=settings,
            mq=mq,
            user_id=user_id,
            profile_id=None,
            params={},
            input_dir=project.input_dir,
            output_dir=project.output_root,
        )
        project.current_job_id = job.id
        db.add(
            IncrementalRound(
                project_id=project.id,
                job_id=job.id,
                output_dir=job.output_dir,
                created_at=_now(),
            )
        )
        project.updated_at = _now()
        db.commit()
        return job
    assert job is not None
    return _rerun_same_job(db, settings, mq, project, job)


def project_detail(db: Session, project: IncrementalProject) -> dict:
    _promote(db, project)
    db.refresh(project)
    files = _files(db, project.id)
    shots: dict[int, dict] = {}
    for row in files:
        item = shots.setdefault(row.shot_index, {"shot_index": row.shot_index, "bands": []})
        item["bands"].append(
            {
                "filename": row.filename,
                "band": row.band,
                "role": row.role,
                "status": row.status,
                "size_bytes": int(row.size_bytes),
            }
        )
    bound = db.get(JobRun, project.current_job_id) if project.current_job_id else None
    copied_at = [row.updated_at for row in files if row.status == "ingested"]
    newer = files_newer_than(bound.started_at, copied_at) if bound is not None else False
    # 一个测区只展示绑定的这一条任务，早前按轮次另建的任务不再列在这里。
    rounds = []
    if bound is not None:
        rounds.append(
            {
                "id": bound.seq,
                "job_id": str(bound.id),
                "job_seq": bound.seq,
                "job_status": bound.status,
                "output_dir": bound.output_dir,
                "created_at": bound.created_at.isoformat() if bound.created_at else None,
                "is_current": bool(
                    bound.status == "succeeded"
                    and project.current_output_dir
                    and bound.output_dir == project.current_output_dir
                ),
            }
        )
    return {
        "id": str(project.id),
        "name": project.name,
        "input_dir": project.input_dir,
        "inbox_dir": project.inbox_dir,
        "output_root": project.output_root,
        "auto_run": project.auto_run,
        "scan_interval_minutes": project.scan_interval_minutes,
        "latest_shot_index": project.latest_shot_index,
        "latest_filename": project.latest_filename,
        "latest_ingested_at": project.latest_ingested_at.isoformat() if project.latest_ingested_at else None,
        "last_scan_at": project.last_scan_at.isoformat() if project.last_scan_at else None,
        "busy": bound is not None and bound.status in BUSY,
        "blocked_by_other": _other_running(db, project.current_job_id),
        "has_new_files": bool(bound is not None and bound.status not in BUSY and newer),
        "waiting_for_job": bool(bound is not None and bound.status in BUSY and bound.status != "queued" and newer),
        "current_job_id": str(project.current_job_id) if project.current_job_id else None,
        "current_output_dir": project.current_output_dir,
        "job": None
        if bound is None
        else {
            "id": str(bound.id),
            "seq": bound.seq,
            "status": bound.status,
            "output_dir": bound.output_dir,
            "message": bound.message,
        },
        "shots": [shots[key] for key in sorted(shots)],
        "rounds": rounds,
        "incoming": sum(1 for row in files if row.status == "incoming"),
        "ready": sum(1 for row in files if row.status == "ready"),
        "ingested": sum(1 for row in files if row.status == "ingested"),
    }


_scan_started = False


def tick_due_projects() -> None:
    """到点的测区扫描入库。打开了自动拼的，在唯一任务上接着拼。"""
    from app.config import get_settings
    from app.db import SessionLocal
    from app.services.deps import get_mq

    db = SessionLocal()
    try:
        settings = get_settings()
        mq = get_mq()
        now = _now()
        for project in list_projects(db):
            interval = max(1, int(project.scan_interval_minutes or 5))
            last = project.last_scan_at
            due = last is None or (now - _aware(last)) >= timedelta(minutes=interval)
            try:
                if due:
                    sweep(db, project)
                if project.auto_run:
                    launch_mosaic(db, settings, mq, project, user_id=project.created_by, manual=False)
            except HTTPException as exc:
                log.warning("增量测区 %s: %s", project.name, exc.detail)
                db.rollback()
            except Exception:
                log.exception("增量测区 %s 扫描失败", project.name)
                db.rollback()
    finally:
        db.close()


def start_inbox_scan() -> None:
    """后端自己按间隔扫描，不依赖页面开着。"""
    global _scan_started
    if _scan_started:
        return
    _scan_started = True

    def loop() -> None:
        while True:
            try:
                tick_due_projects()
            except Exception:
                log.exception("增量收件扫描失败")
            time.sleep(20)

    threading.Thread(target=loop, name="incremental-scan", daemon=True).start()
