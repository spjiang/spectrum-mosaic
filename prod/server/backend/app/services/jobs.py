from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import JobRun, JobRunLog, ParamDefinition, ParamProfile, ParamProfileValue
from app.presets import RGB_PREVIEW_VALUES
from app.services.mq import MQPublisher
from app.services.resolution import apply_resolution_choice, needs_resolution_choice
from app.services.params import assert_param_values
from app.services.paths import detach_derived_dirs, stamp_run_output_dir, validate_paths
from app.services.stage_steps import persist_stage_steps
from app.services.worker_env import apply_worker_env
from app.services.worker_inspect import heartbeat_holds_job

STAGES = ["S0_io", "S1_catalog", "S2_at", "S3_dense", "S4_dsm", "S5_ortho", "S6_report"]
ACTIVE = {"queued", "running", "paused"}
LIVE = ACTIVE | {"awaiting_continue"}
TERMINAL = {"succeeded", "failed", "cancelled"}
# 运行中必须先暂停再删；排队/等待/已结束可直接删
DELETABLE = {"paused", "queued", "awaiting_continue"} | TERMINAL


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stage_rank(stage: str | None) -> int:
    if not stage or stage not in STAGES:
        return -1
    return STAGES.index(stage)


def _reenter_stage(job: JobRun, stage: str) -> None:
    """重新进入较早阶段。完成标记停在它前面，进度条回到该阶段起点。

    失败在质量报告后再跑正射时，库里的当前阶段仍是质量报告。
    若不退回，界面会一直亮着报告节点，而日志实际在写正射。
    """
    rank = stage_rank(stage)
    job.current_stage = stage
    job.completed_stage = STAGES[rank - 1] if rank > 0 else None
    job.stage_progress = 0.0
    job.global_percent = rank / len(STAGES) * 100.0


def job_deletable(status: str) -> tuple[bool, str]:
    if status == "running":
        return False, "请先暂停再删除"
    if status in DELETABLE:
        return True, ""
    return False, f"当前状态不可删除: {status}"


def _write_control(job: JobRun, *, pause: bool = False, cancel: bool = False) -> None:
    log_dir = Path(job.log_dir) if job.log_dir else Path(job.output_dir) / "log"
    path = log_dir / "control.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pause": pause, "cancel": cancel}), encoding="utf-8")
    except OSError:
        return


def append_run_log(db: Session, job_id: uuid.UUID, message: str, *, level: str = "info", commit: bool = True) -> None:
    db.add(JobRunLog(job_id=job_id, message=message, level=level, created_at=_utcnow()))
    if commit:
        db.commit()


def _fmt_log_ts(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def format_job_logs(db: Session, job: JobRun, *, tail: int = 300) -> str:
    rows = list(
        db.scalars(
            select(JobRunLog)
            .where(JobRunLog.job_id == job.id)
            .order_by(JobRunLog.id.asc())
        )
    )
    db_lines = [f"{_fmt_log_ts(r.created_at)} [{r.level}] {r.message}" for r in rows]
    file_text = ""
    log_dir = Path(job.log_dir) if job.log_dir else None
    candidates = sorted(log_dir.glob("*.log")) if log_dir is not None and log_dir.is_dir() else []
    run_log = Path(job.output_dir) / "log" / "run.log" if job.output_dir else None
    if candidates:
        file_text = candidates[-1].read_text(encoding="utf-8", errors="replace")
    elif run_log is not None and run_log.is_file():
        file_text = run_log.read_text(encoding="utf-8", errors="replace")
    parts: list[str] = []
    if db_lines:
        parts.append("\n".join(db_lines))
    file_text = file_text.strip()
    if file_text:
        parts.append(file_text)
    text = "\n".join(parts).strip()
    if not text:
        return "(暂无日志)"
    lines = text.splitlines()
    return "\n".join(lines[-tail:])


def _dump_snapshot_file(job: JobRun) -> None:
    log_dir = Path(job.log_dir) if job.log_dir else Path(job.output_dir) / "log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "params_snapshot.json").write_text(
            json.dumps(job.params_snapshot or {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def parse_memory_gb(params: dict[str, Any] | None) -> float | None:
    raw = (params or {}).get("memory_gb")
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    return val


def parse_cpus(params: dict[str, Any] | None) -> int | None:
    raw = (params or {}).get("cpus")
    if raw is None or raw == "":
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    return val


def parse_gpu(params: dict[str, Any] | None) -> str:
    """auto / off / 卡号，与 Worker 的 ms_mosaic.gpu.parse_gpu_setting 同一规则。"""
    raw = (params or {}).get("gpu")
    if raw is None:
        return "auto"
    text = str(raw).strip().lower()
    if text in ("off", "none", "cpu", "false", "no"):
        return "off"
    if text.isdigit():
        return str(int(text))
    return "auto"


def parse_gpu_memory_gb(params: dict[str, Any] | None) -> float | None:
    raw = (params or {}).get("gpu_memory_gb")
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    return val


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def locate_report_pdf(job: Any) -> Path | None:
    """按实际写出位置找质量报告。旧任务把路径记在成果子目录下，文件其实在输出根目录。"""
    candidates: list[Path] = []
    stored = str(getattr(job, "report_pdf_path", None) or "").strip()
    if stored:
        candidates.append(Path(stored))
    out = str(getattr(job, "output_dir", None) or "").strip()
    if out:
        candidates.append(Path(out) / "质量报告.pdf")
    products = str(getattr(job, "products_dir", None) or "").strip()
    if products:
        candidates.append(Path(products) / "质量报告.pdf")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def _defaults(db: Session) -> dict[str, Any]:
    rows = db.scalars(select(ParamDefinition)).all()
    return {row.key: row.default_value for row in rows}


def resolve_paths(params: dict[str, Any], input_dir: str, output_dir: str) -> dict[str, str]:
    out = Path(output_dir).expanduser().resolve()
    inp = Path(input_dir).expanduser().resolve()
    products = out / str(params.get("products_dir_name") or "拼图结果")
    return {
        "input_dir": str(inp),
        "output_dir": str(out),
        "cache_dir": str(Path(params["cache_dir"]).expanduser().resolve())
        if params.get("cache_dir")
        else str(out / "cache" / "features"),
        "log_dir": str(Path(params["log_dir"]).expanduser().resolve())
        if params.get("log_dir")
        else str(out / "log"),
        "process_dir": str(Path(params["process_dir"]).expanduser().resolve())
        if params.get("process_dir")
        else str(out / "附件"),
        "products_dir": str(products),
        # 质量报告写在本次输出目录根上，与 pipeline 的 out_dir / 质量报告.pdf 一致
        "report_pdf_path": str(out / "质量报告.pdf"),
    }

def build_snapshot(db: Session, profile_id: int | None, overrides: dict[str, Any]) -> tuple[dict[str, Any], int | None, int | None]:
    snap = _defaults(db)
    profile_version = None
    if profile_id is not None:
        profile = db.get(ParamProfile, profile_id)
        if profile is None:
            raise HTTPException(404, "参数模版不存在")
        profile_version = profile.version
        rows = db.scalars(select(ParamProfileValue).where(ParamProfileValue.profile_id == profile_id)).all()
        for row in rows:
            snap[row.param_key] = row.value
        if profile.preset == "rgb_preview":
            snap.update(RGB_PREVIEW_VALUES)
    if overrides.get("preset") == "rgb_preview":
        snap.update(RGB_PREVIEW_VALUES)
    snap.update({k: v for k, v in overrides.items() if v is not None})
    return snap, profile_id, profile_version


def assert_single_running(db: Session, except_id: uuid.UUID | None = None) -> None:
    running = db.scalar(select(JobRun).where(JobRun.status == "running"))
    if running is not None and (except_id is None or running.id != except_id):
        raise HTTPException(409, f"已有任务运行中: {running.id}")


def create_job(
    db: Session,
    *,
    settings: Settings,
    mq: MQPublisher,
    user_id: int | None,
    profile_id: int | None,
    params: dict[str, Any],
    input_dir: str,
    output_dir: str,
) -> JobRun:
    settings = apply_worker_env(db, settings)
    original_out = output_dir
    output_dir = stamp_run_output_dir(output_dir)
    validate_paths(settings, input_dir, output_dir)
    snap, pid, pver = build_snapshot(db, profile_id, params)
    detach_derived_dirs(snap, original_out)
    snap["input_dir"] = input_dir
    snap["output_dir"] = output_dir
    # 模版默认值不改已经写好的 dsm_gsd；只有倍数、还没写成 GSD 时按航片补上。
    if needs_resolution_choice(params, snap):
        apply_resolution_choice(snap, input_dir, settings)
    assert_param_values(list(db.scalars(select(ParamDefinition))), snap)
    paths = resolve_paths(snap, input_dir, output_dir)
    for k, v in paths.items():
        snap[k] = v

    queued = db.scalar(select(JobRun).where(JobRun.status == "queued"))
    job = JobRun(
        id=uuid.uuid4(),
        created_by=user_id,
        profile_id=pid,
        profile_version=pver,
        status="queued",
        params_snapshot=snap,
        input_dir=paths["input_dir"],
        output_dir=paths["output_dir"],
        cache_dir=paths["cache_dir"],
        log_dir=paths["log_dir"],
        process_dir=paths["process_dir"],
        products_dir=paths["products_dir"],
        report_pdf_path=paths["report_pdf_path"],
        memory_gb=parse_memory_gb(snap),
        cpus=parse_cpus(snap),
        gpu=parse_gpu(snap),
        gpu_memory_gb=parse_gpu_memory_gb(snap),
        native_gsd_m=_optional_float(snap.get("native_gsd_m")),
        ortho_gsd_m=_optional_float(snap.get("ortho_gsd_m")),
        message="已入库",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    append_run_log(db, job.id, f"任务已创建 id={job.id} status=queued")
    append_run_log(
        db,
        job.id,
        f"配置已快照入库 模版id={pid} v={pver} 共{len(snap)}项 input={paths['input_dir']} output={paths['output_dir']} 内存={parse_memory_gb(snap) or 0:g}GB CPU={parse_cpus(snap) or 0} GPU={parse_gpu(snap)} 显存={parse_gpu_memory_gb(snap) or 0:g}GB",
    )
    _dump_snapshot_file(job)

    # 先落库再投递；队列失败不能丢掉这条运行记录
    try:
        assert_single_running(db)
        job.status = "running"
        job.started_at = _utcnow()
        job.updated_at = _utcnow()
        job.run_attempt = 1
        job.message = "已下发 worker"
        db.commit()
        append_run_log(db, job.id, "任务进入 running，准备投递 mosaic.jobs")
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
            job.finished_at = _utcnow()
            job.message = f"已入库，队列投递失败: {exc}"
            job.updated_at = _utcnow()
            db.commit()
    except HTTPException:
        wait_msg = f"已有任务运行中，本任务排队等待" + (f" (先前 queued={queued.id})" if queued else "")
        append_run_log(db, job.id, wait_msg)
        job.message = "排队等待"
        job.updated_at = _utcnow()
        db.commit()
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
            append_run_log(db, job.id, "排队任务已投递 mosaic.jobs")
        except Exception as exc:  # noqa: BLE001
            append_run_log(db, job.id, f"消息队列投递失败: {exc}", level="error")
        job = db.get(JobRun, job.id)
    db.refresh(job)
    return job


def _copy_bytes(src: Path, dest: Path) -> None:
    """复制文件。只读来源，不移动、不硬链接。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.copying")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def _seam_source_dirs(source: JobRun) -> tuple[Path, Path]:
    """正射所在目录，以及拼接线附件所在目录。当前成果为空时用最近一次留档。"""
    snap = source.params_snapshot or {}
    products_name = str(snap.get("products_dir_name") or "拼图结果")
    live = Path(source.products_dir) if source.products_dir else Path(source.output_dir) / products_name
    extras = Path(source.output_dir) / "附件"
    if live.is_dir() and any(p.suffix.lower() == ".tif" for p in live.iterdir() if p.is_file()):
        return live, extras
    index_path = extras / "versions" / "index.json"
    if index_path.is_file():
        try:
            rows = json.loads(index_path.read_text(encoding="utf-8")).get("versions") or []
        except (OSError, json.JSONDecodeError):
            rows = []
        for row in reversed(rows):
            folder = extras / "versions" / str(row.get("id") or "")
            if folder.is_dir() and any(p.suffix.lower() == ".tif" for p in folder.iterdir() if p.is_file()):
                return folder, folder
    raise HTTPException(400, "来源任务没有正射成果，不能建立拼接线处理")


def materialize_seam_workspace(source: JobRun, dest_root: Path) -> dict[str, str]:
    """把来源成果复制到新目录。来源文件的内容和位置都不改。"""
    snap = source.params_snapshot or {}
    products_name = str(snap.get("products_dir_name") or "拼图结果")
    src_products, src_extras = _seam_source_dirs(source)
    if dest_root.exists():
        raise HTTPException(400, "输出目录已存在")
    dest_products = dest_root / products_name
    dest_products.mkdir(parents=True)
    for item in src_products.iterdir():
        if item.is_file() and item.suffix.lower() in {".tif", ".tiff"}:
            _copy_bytes(item, dest_products / item.name)
    extras = dest_root / "附件"
    extras.mkdir()
    for name in ("seamlines.geojson", "seam_preview.png", "seam_preview.json"):
        src = src_extras / name
        if src.is_file():
            _copy_bytes(src, extras / name)
    (dest_root / "logs").mkdir()
    (dest_root / "process").mkdir()
    return {
        "output_dir": str(dest_root),
        "products_dir": str(dest_products),
        "log_dir": str(dest_root / "logs"),
        "process_dir": str(dest_root / "process"),
        "cache_dir": str(source.cache_dir or ""),
    }


def create_seam_task(db: Session, *, user_id: int | None, source_id: uuid.UUID) -> JobRun:
    """新建一条拼接线处理：复制来源成果，生成独立任务，不投递计算。"""
    source = db.get(JobRun, source_id)
    if source is None:
        raise HTTPException(404, "来源任务不存在")
    if (source.params_snapshot or {}).get("job_kind") == "seam":
        raise HTTPException(400, "请选择正射任务，不要选择拼接线处理记录")
    if source.status != "succeeded":
        raise HTTPException(400, "来源任务还没有成功成果")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(source.output_dir).parent / f"seam_{stamp}"
    n = 2
    while dest.exists():
        dest = dest.with_name(f"seam_{stamp}_{n}")
        n += 1
    paths = materialize_seam_workspace(source, dest)
    snap = dict(source.params_snapshot or {})
    snap.update(
        {
            "job_kind": "seam",
            "source_job_id": str(source.id),
            "source_seq": int(source.seq),
            "input_dir": source.input_dir,
            "output_dir": paths["output_dir"],
            "products_dir": paths["products_dir"],
        }
    )
    snap.pop("seam_edit_path", None)
    snap.pop("seam_prefer_ids", None)
    timings = {
        key: value
        for key, value in dict(source.stage_timings or {}).items()
        if key != "_open" and not isinstance(value, dict)
    }
    steps = dict(source.stage_steps or {})
    now = _utcnow()
    job = JobRun(
        id=uuid.uuid4(),
        created_by=user_id,
        profile_id=source.profile_id,
        profile_version=source.profile_version,
        status="succeeded",
        run_attempt=1,
        params_snapshot=snap,
        input_dir=source.input_dir,
        output_dir=paths["output_dir"],
        cache_dir=paths["cache_dir"] or None,
        log_dir=paths["log_dir"],
        process_dir=paths["process_dir"],
        products_dir=paths["products_dir"],
        current_stage=source.current_stage or "S6_report",
        completed_stage=source.completed_stage or source.current_stage or "S6_report",
        global_percent=100,
        stage_timings=timings,
        stage_steps=steps,
        stage_progress=100,
        message=f"拼接线处理 · 来源任务 #{source.seq}",
        n_shots=source.n_shots,
        memory_gb=source.memory_gb,
        cpus=source.cpus,
        gpu=source.gpu,
        gpu_memory_gb=source.gpu_memory_gb,
        native_gsd_m=source.native_gsd_m,
        ortho_gsd_m=source.ortho_gsd_m,
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    append_run_log(db, job.id, f"拼接线处理已建立，来源任务 #{source.seq}，成果已复制，来源文件未改动")
    return job


def list_seam_tasks(db: Session) -> list[JobRun]:
    rows = list(db.scalars(select(JobRun).order_by(JobRun.created_at.desc()).limit(200)))
    return [row for row in rows if (row.params_snapshot or {}).get("job_kind") == "seam"]


def _existing_dsm(snap: dict[str, Any]) -> str | None:
    products = snap.get("products_dir")
    candidates: list[Path] = []
    if products:
        candidates.append(Path(str(products)) / "DSM.tif")
    out = snap.get("output_dir")
    if out:
        candidates.append(Path(str(out)) / "拼图结果" / "DSM.tif")
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return None


def _existing_at(snap: dict[str, Any]) -> str | None:
    candidates: list[Path] = []
    cache = snap.get("cache_dir")
    if cache:
        cache_path = Path(str(cache))
        if cache_path.name == "features":
            candidates.append(cache_path.parent / "at_result.npz")
        candidates.append(cache_path / "at_result.npz")
    out = snap.get("output_dir")
    if out:
        candidates.append(Path(str(out)) / "cache" / "at_result.npz")
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return None


def _snapshot_for_rerun(job: JobRun) -> dict[str, Any]:
    """暂停恢复 / 失败重试：已完成的阶段不再重跑，从下一阶段接着干。"""
    snap = dict(job.params_snapshot or {})
    dsm = _existing_dsm(snap)
    at = _existing_at(snap)
    completed = job.completed_stage
    if dsm and stage_rank(completed) < stage_rank("S4_dsm"):
        completed = "S4_dsm"
    elif at and stage_rank(completed) < stage_rank("S2_at"):
        completed = "S2_at"
    if dsm:
        snap["reuse_dsm"] = dsm
    if completed in STAGES:
        idx = STAGES.index(completed)
        snap["start_stage"] = STAGES[idx + 1] if idx < len(STAGES) - 1 else completed
    return snap


def _publish_job_or_fail(db: Session, mq: MQPublisher, job: JobRun, payload: dict[str, Any], ok_log: str) -> None:
    try:
        mq.publish_job(payload)
        append_run_log(db, job.id, ok_log, commit=False)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.finished_at = _utcnow()
        job.message = f"队列投递失败: {exc}"
        append_run_log(db, job.id, job.message, level="error", commit=False)


def control_job(
    db: Session,
    mq: MQPublisher,
    job_id: uuid.UUID,
    action: str,
    stop_after: str | None = None,
    settings: Settings | None = None,
) -> JobRun:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    action = action.lower()
    if action == "pause":
        if job.status != "running":
            raise HTTPException(400, "仅运行中的任务可暂停")
        _write_control(job, pause=True, cancel=False)
        try:
            mq.publish_control({"schema_version": 1, "job_id": str(job.id), "action": "pause"})
        except Exception as exc:  # noqa: BLE001
            append_run_log(db, job.id, f"暂停控制消息投递失败（已写 control.json）: {exc}", level="error", commit=False)
        held = heartbeat_holds_job(settings or Settings(), job.id)
        if held:
            job.message = "暂停请求已发送，当前阶段结束后生效；暂停后可继续运行"
            append_run_log(db, job.id, "收到暂停请求，将在当前阶段结束后生效", commit=False)
        else:
            job.status = "paused"
            job.message = "包装进程已退出，已立即暂停。可继续运行或清理续跑（会清掉残留进程）"
            append_run_log(db, job.id, "Worker 未持有包装进程，已立即暂停", commit=False)
    elif action == "stop":
        if job.status != "running":
            raise HTTPException(400, "仅运行中的任务可立即停止")
        _write_control(job, pause=True, cancel=False)
        try:
            mq.publish_control({"schema_version": 1, "job_id": str(job.id), "action": "kill"})
        except Exception as exc:  # noqa: BLE001
            append_run_log(db, job.id, f"立即停止的控制消息投递失败（已写 control.json）: {exc}", level="error", commit=False)
        job.status = "paused"
        job.finished_at = None
        job.message = "已立即停止。已写完的成果保留，当前阶段没写完的部分不保留，可继续运行"
        append_run_log(db, job.id, "收到立即停止，已结束计算进程。已写完的文件保留", commit=False)
    elif action == "resume":
        if job.status != "paused":
            raise HTTPException(400, "仅已暂停的任务可继续运行")
        _write_control(job, pause=False, cancel=False)
        try:
            mq.publish_control({"schema_version": 1, "job_id": str(job.id), "action": "resume"})
        except Exception as exc:  # noqa: BLE001
            append_run_log(db, job.id, f"继续运行控制消息投递失败（已写 control.json）: {exc}", level="error", commit=False)
        job.finished_at = None
        job.message = "继续运行已下发"
        held = heartbeat_holds_job(settings or Settings(), job.id)
        if held:
            job.status = "running"
            append_run_log(db, job.id, "Worker 进程仍在，已解除暂停，同一进程继续", commit=False)
        else:
            assert_single_running(db)
            snap = _snapshot_for_rerun(job)
            job.params_snapshot = snap
            job.memory_gb = parse_memory_gb(snap)
            job.cpus = parse_cpus(snap)
            job.gpu = parse_gpu(snap)
            job.gpu_memory_gb = parse_gpu_memory_gb(snap)
            job.status = "running"
            job.run_attempt += 1
            _publish_job_or_fail(
                db,
                mq,
                job,
                {
                    "schema_version": 1,
                    "job_id": str(job.id),
                    "run_attempt": job.run_attempt,
                    "params_snapshot": snap,
                    "action": "resume",
                },
                "Worker 已退出，已重新投递并从下一阶段继续",
            )
    elif action == "retry":
        if job.status != "failed":
            raise HTTPException(400, "仅失败任务可重试")
        assert_single_running(db)
        snap = _snapshot_for_rerun(job)
        job.params_snapshot = snap
        job.memory_gb = parse_memory_gb(snap)
        job.cpus = parse_cpus(snap)
        job.gpu = parse_gpu(snap)
        job.gpu_memory_gb = parse_gpu_memory_gb(snap)
        job.status = "running"
        job.run_attempt += 1
        job.finished_at = None
        job.error_summary = None
        job.message = "失败重试已下发"
        _write_control(job, pause=False, cancel=False)
        _publish_job_or_fail(
            db,
            mq,
            job,
            {
                "schema_version": 1,
                "job_id": str(job.id),
                "run_attempt": job.run_attempt,
                "params_snapshot": snap,
                "action": "retry",
            },
            "收到失败重试，沿用原输出目录继续执行",
        )
    elif action == "recover":
        if job.status not in {"running", "failed", "cancelled", "paused"}:
            raise HTTPException(400, "仅卡住、失败、取消或暂停的任务可清理续跑")
        if job.status == "running" and heartbeat_holds_job(settings or Settings(), job.id):
            raise HTTPException(400, "包装进程仍在计算该任务，请先暂停；进程已脱离时应点暂停或清理续跑")
        try:
            mq.publish_control({"schema_version": 1, "action": "kill_all"})
        except Exception:  # noqa: BLE001
            append_run_log(db, job.id, "清理空转进程的控制消息投递失败", level="warning", commit=False)
        try:
            mq.purge_jobs()
        except Exception:  # noqa: BLE001
            pass
        dsm = _existing_dsm(dict(job.params_snapshot or {}))
        if dsm and stage_rank(job.completed_stage) < stage_rank("S4_dsm"):
            job.completed_stage = "S4_dsm"
        snap = _snapshot_for_rerun(job)
        job.params_snapshot = snap
        job.memory_gb = parse_memory_gb(snap)
        job.cpus = parse_cpus(snap)
        job.gpu = parse_gpu(snap)
        job.gpu_memory_gb = parse_gpu_memory_gb(snap)
        assert_single_running(db, except_id=job.id)
        job.status = "running"
        job.run_attempt += 1
        job.finished_at = None
        job.error_summary = None
        job.message = "已清理空转进程，从已有成果续跑"
        _write_control(job, pause=False, cancel=False)
        _publish_job_or_fail(
            db,
            mq,
            job,
            {
                "schema_version": 1,
                "job_id": str(job.id),
                "run_attempt": job.run_attempt,
                "params_snapshot": snap,
                "action": "recover",
            },
            "清理空转后重新投递，复用已有 DSM 继续正射",
        )
    elif action == "cancel":
        if job.status in TERMINAL:
            raise HTTPException(400, "任务已结束")
        _stop_worker(mq, job)
        job.status = "cancelled"
        job.finished_at = _utcnow()
        job.message = "已取消，已停止 Worker 进程"
    elif action == "continue":
        if job.status != "awaiting_continue":
            raise HTTPException(400, "仅 awaiting_continue 可继续下一阶段")
        snap = dict(job.params_snapshot)
        if stop_after:
            snap["stop_after_stage"] = stop_after
            snap["run_mode"] = "until_stage"
        else:
            # advance one stage in step mode or clear stop to go full remaining
            completed = job.completed_stage or snap.get("start_stage") or "S0_io"
            if completed not in STAGES:
                raise HTTPException(400, f"未知 completed_stage: {completed}")
            idx = STAGES.index(completed)
            if idx >= len(STAGES) - 1:
                job.status = "succeeded"
                job.finished_at = _utcnow()
                job.message = "已达最后阶段"
                job.updated_at = _utcnow()
                db.commit()
                db.refresh(job)
                return job
            next_stage = STAGES[idx + 1]
            snap["start_stage"] = next_stage
            if snap.get("run_mode") == "step":
                snap["stop_after_stage"] = next_stage
                snap["run_mode"] = "until_stage"
            elif snap.get("run_mode") == "until_stage":
                # user continues beyond previous stop → run one more stage then wait again unless stop_after passed
                snap["stop_after_stage"] = next_stage
            else:
                snap["run_mode"] = "full"
                snap["stop_after_stage"] = None
        job.params_snapshot = snap
        job.memory_gb = parse_memory_gb(snap)
        job.cpus = parse_cpus(snap)
        job.gpu = parse_gpu(snap)
        job.gpu_memory_gb = parse_gpu_memory_gb(snap)
        assert_single_running(db)
        job.status = "running"
        job.run_attempt += 1
        job.started_at = job.started_at or _utcnow()
        job.finished_at = None
        mq.publish_job(
            {
                "schema_version": 1,
                "job_id": str(job.id),
                "run_attempt": job.run_attempt,
                "params_snapshot": snap,
                "action": "continue",
            }
        )
        job.message = "继续执行已下发"
        append_run_log(db, job.id, "收到继续请求，已下发下一阶段", commit=False)
    else:
        raise HTTPException(400, f"未知动作 {action}")
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def evaluate_job(job: JobRun, n: int) -> dict[str, Any]:
    from app.services.gps_check import load_pose_and_gps, ortho_gsd_of, pixel_errors, summarize

    if n < 1 or n > 500:
        raise HTTPException(400, "评估点数须在 1 到 500 之间")
    at = _existing_at(dict(job.params_snapshot or {}))
    if not at:
        raise HTTPException(400, "还没有空三检查点，跑完空三后才能评估")
    pose, gps, _crs = load_pose_and_gps(Path(at))
    gsd = ortho_gsd_of(job.ortho_gsd_m, dict(job.params_snapshot or {}))
    return summarize(pixel_errors(pose, gps, gsd), n=n, gsd_m=gsd)


def reseam_job(
    db: Session,
    mq: MQPublisher,
    job_id: uuid.UUID,
    prefer_ids: str,
    regions: list | None = None,
    clear_edits: bool = False,
) -> JobRun:
    """按用户指定的影像重算拼接线。复用 DSM，从正射重跑，图割轮数不变。"""
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status in {"queued", "running"}:
        raise HTTPException(400, "任务还在跑，结束后才能改拼接线")
    text = (prefer_ids or "").strip().replace("，", ",")
    snap = dict(job.params_snapshot or {})
    snap.pop("seam_cancel_token", None)
    snap["seam_prefer_ids"] = text
    from app.services.seam_map import (
        archive_current_products,
        clear_seam_edit,
        describe_edit,
        restore_archived_products,
        write_seam_edit,
    )

    if clear_edits:
        from app.services.seam_map import EDIT_NAME, _extras_dir

        saved = (_extras_dir(job) / EDIT_NAME).is_file() or bool(snap.get("seam_edit_path"))
        if not saved:
            raise HTTPException(400, "当前就是自动拼接线，没有需要重算的改动")
        clear_seam_edit(job)
        snap.pop("seam_edit_path", None)
    elif regions:
        snap["seam_edit_path"] = write_seam_edit(job, regions)
    dsm_before = _existing_dsm(snap)
    if not dsm_before:
        raise HTTPException(400, "没有 DSM，不能只重算拼接线")
    archived = archive_current_products(job, describe_edit(job, regions, clear_edits))
    snap["reuse_dsm"] = (archived or {}).get("dsm") or dsm_before
    snap["start_stage"] = "S5_ortho"
    assert_single_running(db, except_id=job.id)
    job.params_snapshot = snap
    job.memory_gb = parse_memory_gb(snap)
    job.cpus = parse_cpus(snap)
    job.gpu = parse_gpu(snap)
    job.gpu_memory_gb = parse_gpu_memory_gb(snap)
    _reenter_stage(job, "S5_ortho")
    job.status = "running"
    job.run_attempt += 1
    job.finished_at = None
    job.error_summary = None
    kept = f"，上一版已保留为{archived['label']}" if archived else ""
    if clear_edits:
        job.message = f"按自动拼接线重算正射{kept}"
    elif regions:
        job.message = f"按图上 {len(regions)} 块范围重算拼接线{kept}"
    else:
        job.message = ("按指定影像重算拼接线" if text else "按自动拼接线重算正射") + kept
    _write_control(job, pause=False, cancel=False)
    _publish_job_or_fail(
        db,
        mq,
        job,
        {
            "schema_version": 1,
            "job_id": str(job.id),
            "run_attempt": job.run_attempt,
            "params_snapshot": snap,
            "action": "reseam",
        },
        job.message,
    )
    if job.status == "failed" and archived:
        restore_archived_products(job, archived)
        snap["reuse_dsm"] = dsm_before
        job.params_snapshot = snap
        job.message = "队列投递失败，拼图结果已放回，没有另存版本"
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def branch_seam_copy(db: Session, job_id: uuid.UUID, version_id: str) -> JobRun:
    """从选定成果复制一份再进入拼接。源文件保持原样，修改记录新增一条。"""
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status in {"queued", "running", "paused"}:
        raise HTTPException(400, "任务还在跑，结束后才能基于某一版复制修改")
    from app.services.seam_map import start_seam_copy

    record = start_seam_copy(job, version_id or "current")
    job.message = str((record.get("change") or {}).get("summary") or "已复制一份成果，可以拼接修改")
    append_run_log(db, job.id, job.message, commit=False)
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def cancel_reseam_job(db: Session, mq: MQPublisher, job_id: uuid.UUID) -> JobRun:
    """停掉正在重算的正射，并把留档的上一版移回拼图结果。"""
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if job.status not in {"queued", "running", "paused"}:
        raise HTTPException(400, "现在没有正在进行的拼接线重算")
    if job.status in {"queued", "running"}:
        _write_control(job, pause=True, cancel=True)
        try:
            mq.publish_control({"schema_version": 1, "job_id": str(job.id), "action": "kill"})
        except Exception as exc:  # noqa: BLE001
            append_run_log(db, job.id, f"取消重算的停止消息投递失败: {exc}", level="error", commit=False)
    from app.services.seam_map import list_product_versions, restore_archived_products

    versions = list_product_versions(job)["versions"]
    if not versions:
        raise HTTPException(400, "没有可退回的上一版成果")
    last = versions[-1]
    restore_archived_products(job, last)
    snap = dict(job.params_snapshot or {})
    products = last.get("products_dir")
    if products and (Path(products) / "DSM.tif").is_file():
        snap["reuse_dsm"] = str(Path(products) / "DSM.tif")
    snap["seam_cancel_token"] = job.run_attempt
    job.params_snapshot = snap
    job.status = "succeeded"
    job.finished_at = _utcnow()
    job.error_summary = None
    job.message = f"已取消拼接线重算，成果已回到{last.get('label') or '上一版'}"
    append_run_log(db, job.id, job.message, commit=False)
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


def _stop_worker(mq: MQPublisher, job: JobRun) -> None:
    """写 control.json 并让 worker 立刻杀掉该任务进程树。"""
    _write_control(job, pause=True, cancel=True)
    mq.publish_control({"schema_version": 1, "job_id": str(job.id), "action": "kill"})


def delete_job(db: Session, mq: MQPublisher, job_id: uuid.UUID) -> None:
    job = db.get(JobRun, job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    ok, reason = job_deletable(job.status)
    if not ok:
        raise HTTPException(400, reason)
    try:
        _stop_worker(mq, job)
    except Exception:  # noqa: BLE001
        _write_control(job, pause=True, cancel=True)
    db.delete(job)
    db.commit()


def delete_all_jobs(db: Session, mq: MQPublisher) -> int:
    """一键清空：立刻杀掉 worker 任务进程，清队列，再删记录。输出目录保留。"""
    jobs = list(db.scalars(select(JobRun)))
    n = len(jobs)
    for job in jobs:
        if job.status not in TERMINAL:
            try:
                _stop_worker(mq, job)
            except Exception:  # noqa: BLE001
                _write_control(job, pause=True, cancel=True)
        db.delete(job)
    try:
        mq.publish_control({"schema_version": 1, "action": "kill_all"})
    except Exception:  # noqa: BLE001
        pass
    try:
        mq.purge_jobs()
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return n


def kill_all_active(db: Session, mq: MQPublisher) -> int:
    """Worker 监控：立刻杀掉计算进程，取消排队/运行/暂停/等待中的任务，历史记录保留。"""
    jobs = list(db.scalars(select(JobRun).where(JobRun.status.in_(tuple(LIVE)))))
    n = 0
    for job in jobs:
        try:
            _stop_worker(mq, job)
        except Exception:  # noqa: BLE001
            _write_control(job, pause=True, cancel=True)
        job.status = "cancelled"
        job.finished_at = _utcnow()
        job.updated_at = _utcnow()
        job.message = "已从 Worker 监控杀死全部任务"
        append_run_log(db, job.id, "Worker 监控：杀死全部任务", level="warning", commit=False)
        n += 1
    try:
        mq.publish_control({"schema_version": 1, "action": "kill_all"})
    except Exception:  # noqa: BLE001
        pass
    try:
        mq.purge_jobs()
    except Exception:  # noqa: BLE001
        pass
    db.commit()
    return n


def estimate_eta_seconds(db: Session, job: JobRun, percent: float) -> int | None:
    """用最近成功任务的耗时按剩余比例估算。没有历史时，用本任务已用时间外推。"""
    if percent >= 100:
        return 0
    if percent < 1:
        return None
    rows = db.scalars(
        select(JobRun)
        .where(
            JobRun.status == "succeeded",
            JobRun.started_at.is_not(None),
            JobRun.finished_at.is_not(None),
        )
        .order_by(JobRun.finished_at.desc())
        .limit(20)
    ).all()
    durations: list[float] = []
    for row in rows:
        if row.id == job.id or row.started_at is None or row.finished_at is None:
            continue
        sec = (_aware(row.finished_at) - _aware(row.started_at)).total_seconds()
        if sec >= 60:
            durations.append(sec)
    remain = (100.0 - percent) / 100.0
    if durations:
        durations.sort()
        mid = durations[len(durations) // 2]
        return max(0, int(mid * remain))
    if job.started_at is not None and percent >= 5:
        elapsed = (_utcnow() - _aware(job.started_at)).total_seconds()
        if elapsed >= 30:
            return max(0, int(elapsed * (100.0 - percent) / percent))
    return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def apply_progress(db: Session, payload: dict[str, Any]) -> JobRun | None:
    job_id = payload.get("job_id")
    if not job_id:
        return None
    job = db.get(JobRun, uuid.UUID(str(job_id)))
    if job is None:
        return None
    incoming_stage = payload.get("stage_id")
    if incoming_stage and stage_rank(str(incoming_stage)) >= stage_rank(job.current_stage):
        job.current_stage = str(incoming_stage)
    if "stage_progress" in payload:
        job.stage_progress = float(payload["stage_progress"])
    if "global_percent" in payload:
        incoming = float(payload["global_percent"])
        job.global_percent = max(float(job.global_percent or 0), incoming)
    if "message" in payload and payload["message"]:
        job.message = str(payload["message"])
        append_run_log(
            db,
            job.id,
            f"{job.current_stage or ''} {payload['message']}".strip(),
            commit=False,
        )
    eta = payload.get("eta_seconds")
    if eta is None:
        eta = estimate_eta_seconds(db, job, float(job.global_percent or 0))
    if eta is not None:
        job.eta_seconds = int(eta)
    job.updated_at = _utcnow()
    db.commit()
    db.refresh(job)
    return job


_CLOCK_STOP = {"failed", "paused", "cancelled", "succeeded", "awaiting_continue"}


def _add_open_slice(timings: dict, stage: str, since: str, now: datetime) -> None:
    """把一段正在跑的时间加进该阶段。停掉之后的空档不在 since 和 now 之间。"""
    try:
        started = datetime.fromisoformat(str(since))
    except ValueError:
        return
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    sec = max(0.0, (now - started).total_seconds())
    prev = timings.get(stage)
    try:
        prev_s = float(prev) if prev is not None and not isinstance(prev, dict) else 0.0
    except (TypeError, ValueError):
        prev_s = 0.0
    timings[stage] = prev_s + sec


def _parse_open_since(open_: dict) -> datetime | None:
    try:
        started = datetime.fromisoformat(str(open_.get("since") or ""))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started


def _last_quiet_log_time(job: JobRun, now: datetime, quiet_s: float = 120) -> datetime | None:
    """取现在之前、已经安静了一段时间的最后一条日志时间。

    重新开跑时，新日志就在这几秒里。用它当上一趟的结束时间，中断空档不会被加进去。
    """
    from app.services.stage_steps import _parse_ts, read_run_log

    text = read_run_log(getattr(job, "output_dir", None), getattr(job, "log_dir", None))
    latest: datetime | None = None
    for line in text.splitlines():
        ts = _parse_ts(line)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() < quiet_s:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def _sync_stage_clock(job: JobRun, event: str | None, stage: str | None, was_running: bool) -> None:
    """阶段耗时只累计进程真的在跑的时段。失败、暂停、杀掉之后不再往下加。"""
    timings = dict(job.stage_timings or {})
    raw = timings.get("_open")
    open_ = raw if isinstance(raw, dict) else None
    now = _utcnow()
    if event in _CLOCK_STOP:
        if open_ and open_.get("stage") and open_.get("since"):
            end = now
            since = _parse_open_since(open_)
            # 进程早已不在，停按钮是事后点的。加到最后一条旧日志为止，不把关机空档算进去。
            if since is not None and (now - since).total_seconds() > 120:
                if not heartbeat_holds_job(Settings(), job.id):
                    last = _last_quiet_log_time(job, now)
                    if last is not None and last > since:
                        end = last
            _add_open_slice(timings, str(open_["stage"]), str(open_["since"]), end)
        timings.pop("_open", None)
        job.stage_timings = timings
        return
    if event == "stage_done":
        if open_ and (not stage or open_.get("stage") == stage):
            timings.pop("_open", None)
            job.stage_timings = timings
        return
    if event == "running" and stage:
        same = bool(was_running and open_ and open_.get("stage") == stage)
        since = _parse_open_since(open_) if open_ else None
        stale = bool(same and since is not None and (now - since).total_seconds() > 120)
        if stale and open_ and since is not None:
            last = _last_quiet_log_time(job, now)
            if last is not None and last > since:
                _add_open_slice(timings, str(open_["stage"]), str(open_["since"]), last)
            timings["_open"] = {"stage": str(stage), "since": now.isoformat()}
            job.stage_timings = timings
            return
        if not same:
            timings["_open"] = {"stage": str(stage), "since": now.isoformat()}
            job.stage_timings = timings


def apply_event(db: Session, payload: dict[str, Any]) -> JobRun | None:
    job_id = payload.get("job_id")
    if not job_id:
        return None
    job = db.get(JobRun, uuid.UUID(str(job_id)))
    if job is None:
        return None
    event = payload.get("event")
    stage = payload.get("stage_id")
    if (job.params_snapshot or {}).get("seam_cancel_token") and event in {
        "running",
        "paused",
        "cancelled",
        "failed",
        "succeeded",
        "awaiting_continue",
    }:
        return job
    was_running = job.status == "running"
    if event == "stage_done" and stage:
        if stage_rank(str(stage)) >= stage_rank(job.completed_stage):
            job.completed_stage = str(stage)
        if stage_rank(str(stage)) >= stage_rank(job.current_stage):
            job.current_stage = str(stage)
        timings = dict(job.stage_timings or {})
        if "elapsed_s" in payload:
            # 重试会再报一次耗时。累加，不能用这次的数盖掉上次已经记下的。
            prev = timings.get(str(stage))
            try:
                prev_s = float(prev) if prev is not None else 0.0
            except (TypeError, ValueError):
                prev_s = 0.0
            timings[str(stage)] = prev_s + float(payload["elapsed_s"])
        job.stage_timings = timings
    if event == "awaiting_continue":
        job.status = "awaiting_continue"
        job.message = payload.get("message") or "阶段完成，等待继续"
    elif event == "succeeded":
        job.status = "succeeded"
        job.global_percent = 100.0
        job.finished_at = _utcnow()
        job.message = payload.get("message") or "完成"
    elif event == "failed":
        # 立即停止已经把任务标成暂停。随后进程被杀掉时补报的失败不能把暂停盖掉。
        if job.status not in {"paused", "cancelled", "succeeded"}:
            job.status = "failed"
            job.error_summary = payload.get("error") or payload.get("message")
            job.finished_at = _utcnow()
    elif event == "cancelled":
        job.status = "cancelled"
        job.finished_at = _utcnow()
    elif event == "paused":
        job.status = "paused"
        job.message = payload.get("message") or "已暂停，可继续运行"
    elif event == "running":
        job.status = "running"
        job.started_at = job.started_at or _utcnow()
        if stage and stage_rank(str(stage)) >= 0:
            incoming = str(stage)
            if stage_rank(incoming) < stage_rank(job.current_stage):
                _reenter_stage(job, incoming)
            else:
                job.current_stage = incoming
    if "n_shots" in payload:
        job.n_shots = payload["n_shots"]
    _sync_stage_clock(job, event, str(stage) if stage else None, was_running)
    job.updated_at = _utcnow()
    if event and event != "stage_log":
        extra = payload.get("error") or payload.get("message") or ""
        append_run_log(
            db,
            job.id,
            f"事件 {event}" + (f" {stage}" if stage else "") + (f" {extra}" if extra else ""),
            level="error" if event == "failed" else "info",
            commit=False,
        )
    try:
        persist_stage_steps(job)
    except Exception:
        logging.getLogger("uvicorn.error").exception("保存阶段步骤失败")
    db.commit()
    db.refresh(job)
    return job
