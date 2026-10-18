"""任务 GPU 预算：探测显卡、选卡、显存上限，以及退回 CPU 的原因。

没有 CuPy、没有卡或卡太老时一律退回 CPU，任务不因 GPU 失败。
一个阶段的 GPU 实现要先通过对拍验收（见 docs/specs/2026-10-17-gpu-acceleration-design.md 第 6 节）
才进 VALIDATED_STAGES；没进白名单的阶段即使有卡也走 CPU。
"""

from __future__ import annotations

from dataclasses import dataclass

AUTO_FRACTION = 0.85
MIN_CAPABILITY = (7, 0)

# 通过三层验证的阶段。新增前必须先过对拍、小窗口验证台和全量比对报告。
VALIDATED_STAGES: frozenset[str] = frozenset()

_MB = 2**20
_GB = 2**30


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    total_mb: int
    free_mb: int
    capability: tuple[int, int]


@dataclass(frozen=True)
class GpuChoice:
    device: int | None
    budget_bytes: int
    name: str
    reason: str


def parse_gpu_setting(raw: object) -> str:
    """auto / off / 卡号。非法值按 auto。"""
    if raw is None:
        return "auto"
    text = str(raw).strip().lower()
    if text in ("off", "none", "cpu", "false", "no"):
        return "off"
    if text.isdigit():
        return str(int(text))
    return "auto"


def probe() -> tuple[list[GpuInfo], str | None]:
    """列出可见显卡。受 CUDA_VISIBLE_DEVICES 限制。"""
    try:
        import cupy
    except Exception:  # noqa: BLE001
        return [], "未安装 GPU 运行库"
    if cupy is None:
        return [], "未安装 GPU 运行库"
    try:
        runtime = cupy.cuda.runtime
        count = int(runtime.getDeviceCount())
    except Exception as exc:  # noqa: BLE001
        return [], f"GPU 驱动不可用（{exc}）"
    if count <= 0:
        return [], "未检测到显卡"
    gpus: list[GpuInfo] = []
    for i in range(count):
        try:
            props = runtime.getDeviceProperties(i)
            with cupy.cuda.Device(i) as dev:
                free, total = dev.mem_info
        except Exception:  # noqa: BLE001
            continue
        name = props.get("name", b"")
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        gpus.append(
            GpuInfo(
                index=i,
                name=str(name).strip("\x00 "),
                total_mb=int(total) // _MB,
                free_mb=int(free) // _MB,
                capability=(int(props.get("major", 0)), int(props.get("minor", 0))),
            )
        )
    if not gpus:
        return [], "显卡信息读取失败"
    return gpus, None


def _cpu(reason: str) -> GpuChoice:
    return GpuChoice(device=None, budget_bytes=0, name="", reason=reason)


def choose(
    setting: str,
    memory_gb: float | None,
    gpus: list[GpuInfo],
    missing_reason: str | None,
    *,
    stage: str,
    validated: frozenset[str],
) -> GpuChoice:
    """按任务设置和探测结果决定这个阶段用哪张卡。纯函数，便于在无卡机器上测试。"""
    setting = parse_gpu_setting(setting)
    if setting == "off":
        return _cpu("任务设置为不用 GPU")
    if not gpus:
        return _cpu(missing_reason or "未检测到显卡")
    if stage not in validated:
        return _cpu("该阶段的 GPU 实现尚未通过验收")
    usable = [g for g in gpus if g.capability >= MIN_CAPABILITY]
    if not usable:
        need = ".".join(str(v) for v in MIN_CAPABILITY)
        return _cpu(f"显卡计算能力低于 {need}")
    if setting == "auto":
        card = max(usable, key=lambda g: (g.free_mb, -g.index))
    else:
        wanted = int(setting)
        card = next((g for g in usable if g.index == wanted), None)
        if card is None:
            return _cpu(f"指定的 GPU {wanted} 不存在或不可用")
    free_bytes = card.free_mb * _MB
    try:
        gb = float(memory_gb) if memory_gb is not None else 0.0
    except (TypeError, ValueError):
        gb = 0.0
    budget = int(gb * _GB) if gb > 0 else int(free_bytes * AUTO_FRACTION)
    budget = min(budget, free_bytes)
    if budget <= 0:
        return _cpu(f"GPU {card.index} 没有空闲显存")
    return GpuChoice(device=card.index, budget_bytes=budget, name=card.name, reason="")


def describe(stage_label: str, choice: GpuChoice) -> str:
    if choice.device is None:
        return f"{stage_label}：{choice.reason}，使用 CPU"
    gb = choice.budget_bytes / _GB
    return f"{stage_label}：GPU {choice.device} {choice.name}，显存上限 {gb:.1f} GB"


_job_setting: str = "auto"
_job_memory_gb: float | None = None


def set_job_gpu(setting: object, memory_gb: float | None) -> None:
    """记下这次任务的 GPU 设置。None 表示恢复默认（auto、显存自动）。"""
    global _job_setting, _job_memory_gb
    _job_setting = parse_gpu_setting(setting)
    try:
        gb = float(memory_gb) if memory_gb is not None else 0.0
    except (TypeError, ValueError):
        gb = 0.0
    _job_memory_gb = gb if gb > 0 else None


def trial_stages() -> frozenset[str]:
    """验收对拍时临时放开的阶段（环境变量 MOSAIC_GPU_TRIAL_STAGES，逗号分隔）。生产不设。"""
    import os

    raw = os.environ.get("MOSAIC_GPU_TRIAL_STAGES", "")
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def select(stage: str) -> GpuChoice:
    gpus, reason = probe()
    trial = trial_stages() - VALIDATED_STAGES
    choice = choose(_job_setting, _job_memory_gb, gpus, reason, stage=stage, validated=VALIDATED_STAGES | trial)
    if choice.device is not None and stage in trial:
        return GpuChoice(choice.device, choice.budget_bytes, f"{choice.name}（验收试运行）", choice.reason)
    return choice


SUMMARY_TTL_S = 60.0
_summary_cache: tuple[float, dict] | None = None


def parse_nvidia_smi(text: str) -> list[GpuInfo]:
    """解析 nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap --format=csv,noheader,nounits。"""
    gpus: list[GpuInfo] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            major, _, minor = parts[4].partition(".")
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    total_mb=int(float(parts[2])),
                    free_mb=int(float(parts[3])),
                    capability=(int(major), int(minor or 0)),
                )
            )
        except ValueError:
            continue
    return gpus


def _runtime_installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("cupy") is not None
    except (ImportError, ValueError):
        return False


def _query_nvidia_smi() -> tuple[list[GpuInfo], str | None]:
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if exe is None:
        return [], "未检测到显卡"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total,memory.free,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"GPU 驱动不可用（{exc}）"
    gpus = parse_nvidia_smi(out)
    return gpus, None if gpus else "未检测到显卡"


def summary(now: float | None = None) -> dict:
    """给节点页的显卡概况。不建 CUDA 上下文，避免心跳进程占显存。"""
    global _summary_cache
    import time

    t = time.monotonic() if now is None else now
    if _summary_cache is not None and t - _summary_cache[0] < SUMMARY_TTL_S:
        return _summary_cache[1]
    gpus, reason = _query_nvidia_smi()
    runtime = _runtime_installed()
    if gpus and not runtime:
        reason = "未安装 GPU 运行库"
    data = {
        "runtime": runtime,
        "available": bool(gpus) and runtime,
        "reason": reason or "",
        "validated_stages": sorted(VALIDATED_STAGES),
        "devices": [
            {
                "index": g.index,
                "name": g.name,
                "total_mb": g.total_mb,
                "free_mb": g.free_mb,
                "capability": f"{g.capability[0]}.{g.capability[1]}",
            }
            for g in gpus
        ],
    }
    _summary_cache = (t, data)
    return data
