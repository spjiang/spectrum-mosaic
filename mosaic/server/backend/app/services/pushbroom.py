"""推扫拼图任务：建任务、投递推扫 worker、取消重跑、接收进度与事件、列成果。"""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PushbroomJob, PushbroomJobLog, User
from app.services.paths import prepare_job_dirs, stamp_run_output_dir, trial_dirs, validate_paths
from app.services import pushbroom_mounts as mounts
from app.services.pushbroom_check import FlightCheck, quick_check

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
    params = mounts.apply_mount(params, mounts.resolve_for(db, inp.name, check.flight.get("started_at")))
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
    input_dir: str | None = None,
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
    if input_dir:
        q = q.where(PushbroomJob.input_dir == input_dir)
        cq = cq.where(PushbroomJob.input_dir == input_dir)
    total = int(db.scalar(cq) or 0)
    rows = list(db.scalars(q.order_by(PushbroomJob.seq.desc()).offset((page - 1) * page_size).limit(page_size)))
    return rows, total


def active_jobs(db: Session) -> list[PushbroomJob]:
    return list(db.scalars(select(PushbroomJob).where(PushbroomJob.status.in_(ACTIVE))))


def queue_positions(rows: list[Any]) -> dict[uuid.UUID, int]:
    """排队任务前面还有几个：在跑的都算，排队的按入队时间。worker 一次只跑一个。"""
    running = sum(1 for row in rows if row.status in {"running", "cancelling"})
    queued = sorted(
        (row for row in rows if row.status == "queued"),
        key=lambda row: (row.updated_at or row.created_at, row.seq),
    )
    return {row.id: running + index for index, row in enumerate(queued)}


def flight_job_stats(db: Session, owner_id: int | None) -> dict[str, dict[str, Any]]:
    """每个架次目录跑过几次、最近一次、最近一次成功。两次查询取完，不按架次逐个查。"""
    q = select(
        PushbroomJob.input_dir,
        func.count().label("n"),
        func.max(PushbroomJob.seq).label("last"),
        func.max(PushbroomJob.seq).filter(PushbroomJob.status == "succeeded").label("done"),
    ).group_by(PushbroomJob.input_dir)
    if owner_id is not None:
        q = q.where(PushbroomJob.created_by == owner_id)
    stats = list(db.execute(q))
    seqs = {seq for row in stats for seq in (row.last, row.done) if seq is not None}
    jobs = {job.seq: job for job in db.scalars(select(PushbroomJob).where(PushbroomJob.seq.in_(seqs)))} if seqs else {}
    return {
        row.input_dir: {"count": int(row.n), "latest": jobs.get(row.last), "latest_done": jobs.get(row.done)}
        for row in stats
    }


def flight_item(check: dict[str, Any], mount: dict[str, Any], stats: dict[str, Any] | None) -> dict[str, Any]:
    latest = (stats or {}).get("latest")
    done = (stats or {}).get("latest_done")
    return {
        **check,
        "mount": mount,
        "job_count": int((stats or {}).get("count") or 0),
        "latest_job": None
        if latest is None
        else {
            "id": str(latest.id),
            "seq": latest.seq,
            "status": latest.status,
            "global_percent": latest.global_percent,
            "current_stage": latest.current_stage,
            "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
        },
        "preview_job_id": str(done.id) if done is not None and preview_png(done) is not None else None,
    }


def flight_items(db: Session, checks: list[FlightCheck], *, owner_id: int | None) -> list[dict[str, Any]]:
    profiles = mounts.load_profiles(db)
    overrides = mounts.load_overrides(db)
    stats = flight_job_stats(db, owner_id)
    items = []
    for check in checks:
        res = mounts.resolve(profiles, overrides.get(check.name), mounts.flight_date(check.flight.get("started_at")))
        key = str(Path(check.input_dir).resolve())
        items.append(flight_item(check.as_dict(), mounts.mount_out(res), stats.get(key)))
    ok = sorted((i for i in items if i["ok"]), key=lambda i: i["flight"].get("started_at") or "", reverse=True)
    bad = sorted((i for i in items if not i["ok"]), key=lambda i: i["name"])
    return ok + bad


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
        f"{(r.created_at or _utcnow()).astimezone(mounts.CST):%Y-%m-%d %H:%M:%S} [{r.level}] {r.message}"
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


def preview_png(job: PushbroomJob) -> Path | None:
    """成果目录里给人看的预览图。没有就返回空，不把别的 PNG 当成预览。"""
    folder = (Path(job.output_dir) / "products").resolve()
    if not folder.is_dir():
        return None
    found: list[Path] = []
    for path in folder.glob("*_preview.png"):
        resolved = path.resolve()
        if resolved.is_file() and resolved.parent == folder:
            found.append(resolved)
    return sorted(found)[0] if found else None


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


def archive_members(job: PushbroomJob) -> list[tuple[Path, str]]:
    """打包下载的成员：成果和报告，不含过程文件。压缩包里的路径与列表一致。"""
    items = list_files(job)
    if not items:
        raise HTTPException(404, "还没有可下载的成果")
    return [(resolve_file(job, item["path"]), item["path"]) for item in items]


def archive_filename(job: PushbroomJob) -> str:
    raw = (job.title or "").strip() or f"pushbroom-{job.seq}"
    stem = re.sub(r'[\\/:*?"<>|\r\n]+', "_", raw).strip(" ._") or f"pushbroom-{job.seq}"
    return f"{stem}_成果.zip"


def iter_archive(job: PushbroomJob) -> Iterator[bytes]:
    """不落临时包，按存储方式边读边打 zip。大立方体只多出包头，不复制一份。"""
    return iter_archive_members(archive_members(job))


def iter_archive_members(members: list[tuple[Path, str]]) -> Iterator[bytes]:
    """成员路径必须在数据库会话关闭前取好。流式写出时不能再碰任务对象。"""
    sizes = [path.stat().st_size for path, _ in members]
    force_zip64 = any(size >= 0xFFFFFFFF for size in sizes) or sum(sizes) >= 0xFFFFFFFF
    chunks: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=8)
    stop = threading.Event()

    class _Writer:
        def __init__(self) -> None:
            self.written = 0

        def write(self, data: bytes | bytearray | memoryview) -> int:
            if stop.is_set():
                raise BrokenPipeError
            blob = bytes(data)
            chunks.put(blob)
            self.written += len(blob)
            return len(blob)

        def seekable(self) -> bool:
            return False

        def tell(self) -> int:
            return self.written

        def flush(self) -> None:
            return None

    def produce() -> None:
        try:
            with zipfile.ZipFile(_Writer(), "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for path, name in members:
                    if stop.is_set():
                        break
                    modified = time.localtime(path.stat().st_mtime)
                    stamp = modified[:6] if modified.tm_year >= 1980 else (1980, 1, 1, 0, 0, 0)
                    info = zipfile.ZipInfo(filename=name, date_time=stamp)
                    info.flag_bits |= 0x08
                    info.compress_type = zipfile.ZIP_STORED
                    with archive.open(info, "w", force_zip64=force_zip64) as dest, path.open("rb") as src:
                        while True:
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            dest.write(block)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            if not stop.is_set():
                chunks.put(exc)
        finally:
            chunks.put(None)

    threading.Thread(target=produce, name="pushbroom-archive", daemon=True).start()
    try:
        while True:
            item = chunks.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        while True:
            try:
                chunks.get_nowait()
            except queue.Empty:
                break


def resolve_file(job: PushbroomJob, rel: str) -> Path:
    parts = Path(rel or "").parts
    if len(parts) != 2 or parts[0] not in DOWNLOAD_DIRS or parts[1] in {"..", "."}:
        raise HTTPException(404, "文件不存在")
    path = (Path(job.output_dir) / parts[0] / parts[1]).resolve()
    if path.parent != (Path(job.output_dir) / parts[0]).resolve() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return path
