"""从 run.log 拆出每个流程节点里完成的功能、耗时和统计，并写入任务表。"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("uvicorn.error")

STAGES = (
    "S0_io",
    "S1_catalog",
    "S2_at",
    "S3_dense",
    "S4_dsm",
    "S5_ortho",
    "S6_report",
)

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_START = re.compile(r"start (S\d_[A-Za-z0-9_]+)\s*(.*)$")
_SECS = re.compile(r"(\d+(?:\.\d+)?)s")
_PROGRESS = re.compile(r"\d+\s*/\s*\d+\s*块")
_AT_PARALLEL = re.compile(r"空三并行\s+(\d+)\s*进程")
_POOL = re.compile(r"(\d+)\s*块\s*/\s*\d+\s*段\s*/\s*(\d+)\s*进程")


def _parse_ts(text: str) -> datetime | None:
    match = _TS.match(text.strip())
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _message(line: str) -> str:
    text = line.strip()
    text = _TS.sub("", text).strip()
    if " INFO " in text:
        text = text.split(" INFO ", 1)[1]
        if " " in text:
            # job <uuid> 后面才是正文
            parts = text.split(" ", 2)
            if len(parts) == 3 and parts[0] == "job":
                text = parts[2]
    return text.strip()


def _explicit_seconds(text: str) -> float | None:
    found = _SECS.findall(text)
    if not found:
        return None
    return float(found[0])


def _strip_seconds(text: str) -> str:
    return _SECS.sub("", text).replace("，，", "，").strip(" ，,")


def _step(name: str, detail: str, elapsed: float | None, ts: datetime | None, *, status: str = "done") -> dict:
    return {
        "name": name,
        "detail": detail.strip(" ，,"),
        "elapsed_s": elapsed,
        "status": status,
        "_ts": ts,
    }


def _finish_elapsed(steps: list[dict]) -> None:
    """每个子步骤的耗时只取它自己的开始到结束。

    有日志写明的秒数就用那个数。否则用上一条到这一条的间隔。
    阶段入口（正射波段、路径校验）不是一项工作，不占这段时间。
    再次进入同一阶段时，起点改到这次的 start，上一段到这次之间的中断不算。
    """
    prev: datetime | None = None
    for step in steps:
        ts = step.get("_ts")
        anchor = step.pop("_anchor", None)
        restart = step.pop("_restart", False)
        marker = step.pop("_marker", False)
        if restart:
            prev = anchor if isinstance(anchor, datetime) else None
        if marker:
            if ts is not None:
                prev = ts
            step.pop("_ts", None)
            continue
        if step["elapsed_s"] is None and prev is None and isinstance(anchor, datetime):
            prev = anchor
        if step["elapsed_s"] is None and prev is not None and ts is not None:
            step["elapsed_s"] = max(0.0, (ts - prev).total_seconds())
        if ts is not None:
            prev = ts
        step.pop("_ts", None)


def parse_stage_steps(
    text: str,
    *,
    current_stage: str | None = None,
    running: bool = False,
    now: datetime | None = None,
    alive: bool = True,
) -> dict[str, list[dict]]:
    """把日志拆成 stage_id → 功能列表。进度刷屏（10/825 块）不单列。"""
    buckets: dict[str, list[dict]] = {stage: [] for stage in STAGES}
    current: str | None = None
    at_workers: int | None = None
    pending_dense: int | None = None
    ortho_open: dict[str, datetime] = {}
    # 同一阶段再次 start 后，下一行从这次 start 起算，不能去减上一次的时间。
    restart_next: set[str] = set()
    anchors: dict[str, datetime] = {}
    for raw in text.splitlines():
        msg = _message(raw)
        if not msg or "mq publisher" in msg or msg.startswith("RuntimeWarning"):
            continue
        ts = _parse_ts(raw)
        if msg.startswith("start input="):
            if current in buckets:
                restart_next.add(current)
            current = None
            continue
        started = _START.search(msg)
        if started:
            current = started.group(1)
            tail = started.group(2).strip()
            if current in buckets and ts is not None:
                anchors[current] = ts
            if current in buckets and buckets[current]:
                restart_next.add(current)
            if current in buckets and tail and tail not in {"进入主计算", "空三 / 稀疏重建", "密集匹配"}:
                name = _start_name(current, tail)
                steps = buckets[current]
                # 中断后续跑会再打一行 start。上次进入还没有分项时，不另起一行。
                if (
                    steps
                    and steps[-1]["name"] == name
                    and not steps[-1]["detail"]
                    and steps[-1]["elapsed_s"] is None
                ):
                    steps[-1]["_restart"] = True
                    steps[-1]["_ts"] = ts
                else:
                    step = _step(name, "", None, ts)
                    step["_marker"] = True
                    if steps:
                        step["_restart"] = True
                    steps.append(step)
            continue
        if current not in buckets:
            continue
        if _PROGRESS.search(msg):
            continue
        step = _classify(current, msg, ts, ortho_open)
        if step is None:
            parallel = _AT_PARALLEL.search(msg)
            if parallel:
                at_workers = int(parallel.group(1))
            pool = _POOL.search(msg)
            if pool and current == "S3_dense":
                n_proc = int(pool.group(2))
                last = buckets[current][-1] if buckets[current] else None
                if last is not None and last["name"] in {"粗格网", "细格网"} and "进程" not in last["detail"]:
                    last["detail"] = f"{last['detail']} · {n_proc} 个进程" if last["detail"] else f"{n_proc} 个进程"
                else:
                    pending_dense = n_proc
            continue
        if current in restart_next:
            step["_restart"] = True
            restart_next.discard(current)
            if current in anchors:
                step["_anchor"] = anchors[current]
        elif not buckets[current] and current in anchors:
            step["_anchor"] = anchors[current]
        if step["name"] in {"特征提取", "像对匹配"} and at_workers:
            extra = f"{at_workers} 个进程"
            step["detail"] = f"{step['detail']} · {extra}" if step["detail"] else extra
        if step["name"] in {"粗格网", "细格网"} and pending_dense and "进程" not in step["detail"]:
            step["detail"] = f"{step['detail']} · {pending_dense} 个进程" if step["detail"] else f"{pending_dense} 个进程"
            pending_dense = None
        buckets[current].append(step)
    for stage, steps in buckets.items():
        last_ts = steps[-1].get("_ts") if steps else None
        last_name = steps[-1]["name"] if steps else ""
        _finish_elapsed(steps)
        if running and alive and stage == current_stage and steps and steps[-1]["status"] == "done":
            nxt = _next_name(last_name)
            if nxt:
                # 进程还在时，这一步从上一行日志记到现在。进程已经不在，就停在上一行，不能把死机空档算进去。
                elapsed = None
                if now is not None and last_ts is not None:
                    elapsed = max(0.0, (now - last_ts).total_seconds())
                steps.append(
                    {
                        "name": nxt,
                        "detail": "",
                        "elapsed_s": elapsed,
                        "status": "running",
                        "started_at": last_ts.isoformat() if last_ts is not None else None,
                    }
                )
    return buckets


def _start_name(stage: str, tail: str) -> str:
    names = {
        "S0_io": "路径校验",
        "S4_dsm": "写出 DSM",
        "S5_ortho": "正射波段",
        "S6_report": "质量报告",
    }
    if stage in names:
        return names[stage]
    if tail.startswith("复用"):
        return "复用已有成果"
    return tail


def _classify(stage: str, msg: str, ts: datetime | None, ortho_open: dict[str, datetime]) -> dict | None:
    elapsed = _explicit_seconds(msg)
    body = _strip_seconds(msg)
    if stage == "S2_at":
        if body.startswith("曝光"):
            return _step("影像筛选", _screen_detail(body), elapsed, ts)
        if body.startswith("候选像对"):
            return _step("候选像对", _pair_detail(body), elapsed, ts)
        if body.startswith("特征提取"):
            return _step("特征提取", body.removeprefix("特征提取").strip(), elapsed, ts)
        if body.startswith("匹配"):
            return _step("像对匹配", body.removeprefix("匹配").strip(), elapsed, ts)
        if body.startswith("连接点"):
            return _step("连接点", _dot_join(body.removeprefix("连接点")), elapsed, ts)
        if body.startswith("交会"):
            return _step("前方交会", _dot_join(body.removeprefix("交会")), elapsed, ts)
        stage_no = re.match(r"阶段(\d+)\s*(.*)", body)
        if stage_no:
            return _step(f"平差阶段{stage_no.group(1)}", _tidy_ba(stage_no.group(2)), elapsed, ts)
        if body.startswith("空三并行"):
            return None
        if body.startswith("空三"):
            return _step("平差", body.removeprefix("空三").strip(), elapsed, ts)
        if body.startswith("复用空三"):
            return _step("复用空三", body, elapsed, ts)
        return None
    if stage == "S3_dense":
        if body.startswith("DSM GSD") or body.startswith("锁定交付格网"):
            return _step("分辨率", _gsd_detail(body), elapsed, ts)
        if body.startswith("高程搜索半宽"):
            return _step("高程搜索", body.removeprefix("高程搜索半宽").strip(), elapsed, ts)
        if body.startswith("粗格网解出"):
            return _step("粗格网", _dot_join(body.removeprefix("粗格网")), elapsed, ts)
        if body.startswith("密集匹配：") and "块" not in body:
            return _step("细格网", body.removeprefix("密集匹配：").strip(), elapsed, ts)
        if body.startswith("复用 DSM"):
            return _step("复用 DSM", body, elapsed, ts)
        return None
    if stage == "S5_ortho":
        opened = re.search(r"\(([^)]+)\)\s+(\d+)\s*张", body) if body.startswith("===") else None
        if opened and ts is not None:
            ortho_open[opened.group(1)] = ts
            return None
        wrote = re.match(r"已写出\s+\S+（(.+?)\s*完成）", body)
        if wrote:
            band = wrote.group(1)
            if elapsed is None and ts is not None and band in ortho_open:
                elapsed = max(0.0, (ts - ortho_open[band]).total_seconds())
            detail = ""
            return _step(f"正射 {band}", detail, elapsed, ts)
        if body.startswith("多光谱正射并发"):
            return _step("多光谱并发", body.removeprefix("多光谱正射并发").strip(), elapsed, ts)
        return None
    if stage == "S6_report":
        if "比对" in body or "报告" in body:
            return _step("质量报告", body, elapsed, ts)
        return None
    if stage == "S0_io" and "检查路径" in body:
        return _step("检查路径", "", elapsed, ts)
    return None


def _dot_join(text: str) -> str:
    parts = [p.strip() for p in re.split(r"[，,]", text) if p.strip()]
    return " · ".join(parts)


def _screen_detail(text: str) -> str:
    shots = re.search(r"曝光\s+(\d+)\s*→\s*可用\s+(\d+)", text)
    images = re.search(r"影像\s+(\d+)\s*张", text)
    bits: list[str] = []
    if shots:
        bits.append(f"可用 {shots.group(2)} / {shots.group(1)} 曝光")
    if images:
        bits.append(f"{images.group(1)} 张影像")
    dropped = [(k, int(v)) for k, v in re.findall(r"'(\w+)':\s*(\d+)", text) if int(v) > 0]
    if dropped:
        bits.append("剔除 " + "、".join(f"{k} {v}" for k, v in dropped))
    return " · ".join(bits)


def _pair_detail(text: str) -> str:
    count = re.search(r"(\d+)\s*对", text)
    bands = re.search(r"(\d+)\s*个波段", text)
    bits: list[str] = []
    if count:
        bits.append(f"{count.group(1)} 对")
    if bands:
        bits.append(f"{bands.group(1)} 个波段")
    return " · ".join(bits)


def _gsd_detail(text: str) -> str:
    gsd = re.search(r"(\d+\.\d+)\s*m", text)
    if gsd:
        return f"DSM {float(gsd.group(1)):.4f} 米，正射减半"
    return _dot_join(text)


def _tidy_ba(text: str) -> str:
    text = re.sub(r"自由内参=", "内参 ", text)
    text = text.replace("RMS=", "RMS ")
    text = text.replace("剔除观测", "剔除")
    text = text.replace("剩余点", "剩余")
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"\s+(?=RMS |剔除 |剩余 )", text)
    return " · ".join(p.strip() for p in parts if p.strip())


def _next_name(name: str) -> str | None:
    order = {
        "影像筛选": "候选像对",
        "候选像对": "特征提取",
        "特征提取": "像对匹配",
        "像对匹配": "连接点",
        "连接点": "前方交会",
        "前方交会": "平差",
        "平差阶段1": "平差",
        "平差阶段2": "平差",
        "平差阶段3": "平差",
        "分辨率": "高程搜索",
        "高程搜索": "粗格网",
        "粗格网": "细格网",
    }
    if name in order:
        return order[name]
    if name.startswith("平差阶段"):
        return "平差"
    return None


def read_run_log(output_dir: str | None, log_dir: str | None) -> str:
    candidates: list[Path] = []
    if log_dir:
        candidates.append(Path(log_dir) / "run.log")
    if output_dir:
        candidates.append(Path(output_dir) / "log" / "run.log")
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def stage_run_seconds(text: str) -> dict[str, float]:
    """每个节点按自己连续在跑的时段累加：这一段的第一条日志到最后一条。

    同一节点再次 start 时先收口上一段。两段之间是中断或死机，不计入。
    """
    totals = {stage: 0.0 for stage in STAGES}
    current: str | None = None
    start_ts: datetime | None = None
    end_ts: datetime | None = None

    def close() -> None:
        nonlocal start_ts, end_ts
        if current in totals and start_ts is not None and end_ts is not None and end_ts >= start_ts:
            totals[current] += (end_ts - start_ts).total_seconds()
        start_ts = None
        end_ts = None

    for raw in text.splitlines():
        msg = _message(raw)
        if not msg or "mq publisher" in msg or msg.startswith("RuntimeWarning"):
            continue
        ts = _parse_ts(raw)
        # 新进程起来的第一行。它和上一条之间是死机或卡住，不能接到上一段后面。
        if msg.startswith("start input="):
            close()
            current = None
            continue
        started = _START.search(msg)
        if started and started.group(1) in totals:
            new = started.group(1)
            if start_ts is not None:
                close()
            current = new
            if ts is not None:
                start_ts = ts
                end_ts = ts
            continue
        if current in totals and ts is not None:
            if start_ts is None:
                start_ts = ts
            end_ts = ts
    close()
    return totals


def _last_stage_ts(text: str, stage: str) -> datetime | None:
    current: str | None = None
    last: datetime | None = None
    for raw in text.splitlines():
        msg = _message(raw)
        if not msg or "mq publisher" in msg:
            continue
        ts = _parse_ts(raw)
        if msg.startswith("start input="):
            current = None
            continue
        started = _START.search(msg)
        if started and started.group(1) in STAGES:
            current = started.group(1)
            if current == stage and ts is not None:
                last = ts
            continue
        if current == stage and ts is not None:
            last = ts
    return last


def _stages_in_log(text: str) -> set[str]:
    seen: set[str] = set()
    for raw in text.splitlines():
        msg = _message(raw)
        if not msg:
            continue
        started = _START.search(msg)
        if started and started.group(1) in STAGES:
            seen.add(started.group(1))
    return seen


def apply_step_clock(job, text: str, *, alive: bool, now: datetime) -> None:
    """节点耗时是它自己各段连续运行的起止时间。死机到再次开始之间的空档不在里面。

    进程还在时，最后一条日志之后的时间交给页面接着加，并且要求心跳是新的。
    日志里出现过的节点，即使用新算法算出 0，也要盖掉以前记进去的空档。
    """
    totals = stage_run_seconds(text)
    seen = _stages_in_log(text)
    timings = dict(getattr(job, "stage_timings", None) or {})
    current = getattr(job, "current_stage", None) if getattr(job, "status", None) == "running" else None
    for stage, sec in totals.items():
        if sec > 0:
            timings[stage] = sec
            continue
        if stage not in seen:
            continue
        # 日志起止是 0 时，短耗时仍用进程自己记下的数。只有原先那笔长到不可能是这一步（死机空档）才清掉。
        try:
            prev_s = float(timings.get(stage) or 0)
        except (TypeError, ValueError):
            prev_s = 0.0
        if prev_s > 600:
            timings[stage] = 0.0
    last = _last_stage_ts(text, str(current)) if current else None
    if alive and current and last is not None:
        timings["_open"] = {"stage": str(current), "since": last.isoformat(), "beat": now.isoformat()}
    else:
        timings.pop("_open", None)
    job.stage_timings = timings


def steps_payload(output_dir: str | None, log_dir: str | None, *, current_stage: str | None, status: str | None, alive: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    stages = parse_stage_steps(
        read_run_log(output_dir, log_dir),
        current_stage=current_stage,
        running=status == "running",
        now=now,
        alive=alive,
    )
    return {"stages": stages}


def persist_stage_steps(job) -> dict:
    """解析日志并写入 job.stage_steps。节点耗时改成各段连续运行的起止，不含中断。"""
    output = getattr(job, "output_dir", None)
    log_dir = getattr(job, "log_dir", None)
    stored = dict(getattr(job, "stage_steps", None) or {})
    text = read_run_log(output, log_dir) if (output or log_dir) else ""
    if not text:
        return {"stages": stored}
    alive = False
    if getattr(job, "status", None) == "running":
        try:
            from app.config import Settings
            from app.services.worker_inspect import heartbeat_holds_job

            alive = heartbeat_holds_job(Settings(), job.id)
        except Exception:
            alive = False
    now = datetime.now(timezone.utc)
    stages = parse_stage_steps(
        text,
        current_stage=getattr(job, "current_stage", None),
        running=getattr(job, "status", None) == "running",
        now=now,
        alive=alive,
    )
    if not any(stages.values()):
        apply_step_clock(job, text, alive=alive, now=now)
        return {"stages": stored}
    # 拼接线任务的日志只有重跑过的阶段。没出现在新日志里的阶段，保留从原任务复制来的分项。
    merged = dict(stored)
    for stage, steps in stages.items():
        if steps:
            merged[stage] = steps
    before_timings = dict(getattr(job, "stage_timings", None) or {})
    if stored != merged:
        job.stage_steps = merged
    apply_step_clock(job, text, alive=alive, now=now)
    if dict(job.stage_timings or {}) == before_timings and stored == merged:
        return {"stages": merged}
    return {"stages": merged}


def sync_stage_steps(*, only_running: bool) -> None:
    """把已有日志补进数据库。运行中的任务会反复刷，已结束的只补一次。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import JobRun

    db = SessionLocal()
    try:
        query = select(JobRun)
        if only_running:
            query = query.where(JobRun.status == "running")
        changed = False
        for job in db.scalars(query):
            before = dict(job.stage_steps or {})
            before_timings = dict(job.stage_timings or {})
            persist_stage_steps(job)
            if dict(job.stage_steps or {}) != before or dict(job.stage_timings or {}) != before_timings:
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()


_sync_started = False


def start_stage_step_sync() -> None:
    """后台把运行中任务的功能步骤写入数据库。当前这次空三不会发新事件，靠读日志。"""
    global _sync_started
    if _sync_started:
        return
    _sync_started = True

    def loop() -> None:
        first = True
        while True:
            try:
                sync_stage_steps(only_running=not first)
            except Exception:
                log.exception("保存阶段步骤失败")
            first = False
            time.sleep(30)

    threading.Thread(target=loop, name="stage-steps", daemon=True).start()
