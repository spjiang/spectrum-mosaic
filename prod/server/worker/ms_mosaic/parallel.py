"""按格网块并行的执行器。

密集匹配与正射都是「块之间完全独立」的计算，是典型的可并行结构。串行跑
4.5M 格网要 410 s，10 km 条带按面积外推约 2.2 小时，远超 30 分钟目标。

三个实现要点
------------
每个进程独立持有影像缓存
    影像是 numpy 数组，跨进程共享要么走共享内存（需要自己管生命周期），要么
    反复 pickle（比重新读盘还慢）。直接让每个 worker 自己读、自己缓存最简单，
    代价是同一张影像会被多个进程各读一次。

按空间邻近顺序分块
    相邻格网块看到的是同一批影像。若按块号轮转（worker i 拿第 i, i+n, i+2n 块），
    每个 worker 都要扫遍全部影像，缓存全部失效。改成把块按行带切成连续段，
    每段给一个 worker，缓存命中率就上来了。

限制每进程缓存张数
    668 张灰度影像约 2.1 GB。12 个进程各自缓存全量要 25 GB。按每进程
    limit 张封顶，既够用又不至于把内存吃光。
"""

from __future__ import annotations

import itertools
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ms_mosaic.cpu import cpu_cap
from ms_mosaic import memory as _memory
from ms_mosaic.memory import cap_workers, job_memory_gb, process_rss_bytes, resolve_memory_budget

# 调用方不知道影像规模时的保守单进程估计。正射/密集应按缓存字节 + 单块工作内存传入实估值。
TILE_WORKER_MB = 4096
# 剩余内存是实时读的（已扣本进程），预算也扣了本进程 RSS；这里只留其它容器波动的余量。
TILE_RESERVE_MB = 1536
# 单进程除影像缓存外的开销：解释器 + numpy/scipy/cv2/rasterio 约 300 MB，
# 一个 480² 块的多视角栈、图割与金字塔融合（12 视角 RGB ≤280 MB）。
# S810 #18 实测 VmHWM：Color 1039 MB（缓存 432 MB）、多光谱 771～804 MB（缓存 288 MB）。
TILE_WORK_MB = 640

Window = tuple[int, int, int, int]

# 每进程影像缓存张数上限。一个格网块最多用 max_views(10) 张，留几倍余量
# 让相邻块之间能复用，再多就只是占内存。
CACHE_PER_WORKER = 48
# 每进程约分几段任务；被系统回收时只重算在跑的段
JOB_SEGMENTS_PER_WORKER = 16
# 已减到 1 个进程仍连续被回收几次就报内存不足，不再静默重试
MAX_BROKEN_AT_ONE = 3


def tile_worker_mb(image_bytes: int, *, cache: int = CACHE_PER_WORKER, work_mb: int = TILE_WORK_MB) -> int:
    """单进程内存估计：缓存满时的影像字节 + 单块工作内存。"""
    return int(-(-int(cache) * int(image_bytes) // 2**20) + int(work_mb))


def default_workers() -> int:
    """默认进程数。有任务 CPU 预算时按预算封顶，否则留一核给系统。"""
    return cpu_cap()


_LEDGER_LOCK = threading.Lock()
_LEDGER: dict[int, tuple[int, Callable[[], int]]] = {}
_LEDGER_SEQ = itertools.count()


class reserve_memory:
    """本进程内的内存预约：同一任务里并发的波段池、主进程写出彼此可见。

    剩余内存是瞬时值：刚起的子进程还没涨到峰值、主进程写出即将整幅分配，
    别的波段此刻读到的剩余都偏大。预约里尚未被 used_mb() 兑现的部分计为待占用，
    resolve_workers 从预算与剩余里一并扣掉。
    """

    def __init__(self, mb: int, used_mb: Callable[[], int] | None = None) -> None:
        self.mb = max(0, int(mb))
        self._used = used_mb or (lambda: 0)
        self._token: int | None = None

    def __enter__(self) -> "reserve_memory":
        with _LEDGER_LOCK:
            self._token = next(_LEDGER_SEQ)
            _LEDGER[self._token] = (self.mb, self._used)
        return self

    def update(self, mb: int) -> None:
        self.mb = max(0, int(mb))
        if self._token is not None:
            with _LEDGER_LOCK:
                _LEDGER[self._token] = (self.mb, self._used)

    def __exit__(self, *exc) -> None:
        with _LEDGER_LOCK:
            _LEDGER.pop(self._token, None)
        self._token = None


def pending_mb() -> int:
    """已预约、尚未体现在实际常驻里的内存（MB）。"""
    with _LEDGER_LOCK:
        items = list(_LEDGER.values())
    total = 0
    for mb, used in items:
        try:
            total += max(0, mb - int(used()))
        except Exception:  # noqa: BLE001
            total += mb
    return total


def growth_since_now() -> Callable[[], int]:
    """返回「本进程 RSS 相对此刻增长了多少 MB」的函数，供主进程写出预约用。"""
    base = process_rss_bytes() or 0

    def used() -> int:
        return max(0, ((process_rss_bytes() or 0) - base) >> 20)

    return used


def _pids_rss_mb(pids: Iterable[int]) -> int:
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1]) >> 10
                        break
        except (OSError, ValueError, IndexError):
            continue
    return total


def resolve_workers(requested: int | None, *, per_worker_mb: int | None = None) -> int:
    """requested 为 None 取默认值，<=1 表示串行（便于调试与测试）。

    进程数 = CPU ∩（任务预算 − 本进程已占 ∩ 此刻剩余，均再扣本进程内其它预约的待占用）÷ 单进程估计。
    """
    if requested is None:
        n = default_workers()
    else:
        n = cpu_cap(max(1, int(requested)))
    if n <= 1:
        return 1
    pending = pending_mb() << 20
    gb = job_memory_gb()
    budget = resolve_memory_budget(gb if gb is not None and gb > 0 else 0)
    if budget is not None:
        budget = max(0, budget - (process_rss_bytes() or 0) - pending)
    available = _memory.available_bytes()
    if available is not None:
        available = max(0, available - pending)
    return cap_workers(
        n,
        per_worker_mb=int(per_worker_mb or TILE_WORKER_MB),
        reserve_mb=TILE_RESERVE_MB,
        budget_bytes=budget,
        available=available,
    )


def prefer_oom_kill() -> None:
    """让内核在内存不足时优先杀计算子进程，而不是持有整个任务状态的主进程。

    子进程被杀时 map_tiles 能收到 BrokenProcessPool 并减半续跑；主进程被杀任务就整段失败。
    调高 oom_score_adj 不需要特权。
    """
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as fh:
            fh.write("1000")
    except OSError:
        pass


def release_heap() -> None:
    """把 glibc 堆里已释放的空闲页还给系统（malloc_trim）。非 glibc 平台静默跳过。"""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def peak_rss_mb() -> float:
    """本进程生命周期内的峰值常驻内存（MB）。

    优先读 VmHWM：Linux 的 ru_maxrss 会经 fork/exec 继承父进程的峰值，
    spawn 出的子进程报的其实是主进程的数（曾把 1.1 GB 的子进程报成 3.6 GB）。
    """
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:  # noqa: BLE001
        return 0.0


def split_ortho_pools(n_bands: int, tile_workers: int) -> tuple[int, int]:
    """多光谱波段并发：返回 (同时跑几个波段, 每波段多少块进程)。

    总进程约 n_par × per，不超过 tile_workers。Color 仍单独跑（曝光统计更吃内存）。
    """
    w = max(1, int(tile_workers or 1))
    n = max(1, int(n_bands or 1))
    if n == 1 or w <= 2:
        return 1, w
    n_par = min(n, max(2, w // 2))
    per = max(1, w // n_par)
    n_par = min(n, max(1, w // per))
    return n_par, per


def spatial_chunks(windows: Sequence[Window], n_chunks: int) -> list[list[int]]:
    """把格网块按空间邻近切成 n_chunks 段，返回各段的块下标。

    先按 (行, 列) 排序再切连续段，使同一段内的块在地面上相邻，从而共用影像。
    """
    if n_chunks <= 1 or len(windows) <= 1:
        return [list(range(len(windows)))]
    order = sorted(range(len(windows)), key=lambda k: (windows[k][0], windows[k][1]))
    n_chunks = min(n_chunks, len(order))
    size, extra = divmod(len(order), n_chunks)
    chunks, start = [], 0
    for c in range(n_chunks):
        take = size + (1 if c < extra else 0)
        chunks.append(order[start : start + take])
        start += take
    return chunks


@dataclass
class TileJob:
    """一段连续格网块的任务。worker 收到它后按序算完再返回。"""

    indices: list[int]
    windows: list[Window]


_WORKER_STATE: dict[str, Any] = {}


def _init_worker(setup: Callable[[], Any]) -> None:
    """进程启动时建一次性的重型状态（影像缓存等）。"""
    # 每个 worker 都是独立进程，BLAS 再各开多线程会超订 CPU，反而变慢
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    prefer_oom_kill()
    _WORKER_STATE["ctx"] = setup()


def _kill_pool(pool) -> None:
    """只终止这个池自己的子进程；其它波段并发的池不受影响。"""
    if pool is None:
        return
    for proc in list((getattr(pool, "_processes", None) or {}).values()):
        try:
            if proc.is_alive():
                proc.kill()
            proc.join(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _run_job(fn: Callable[[Any, Window], Any], job: TileJob) -> tuple[list[tuple[int, Any]], float]:
    ctx = _WORKER_STATE["ctx"]
    out = []
    for k, w in zip(job.indices, job.windows):
        out.append((k, fn(ctx, w)))
        release_heap()
    return out, peak_rss_mb()


def map_tiles(
    windows: Sequence[Window],
    setup: Callable[[], Any],
    fn: Callable[[Any, Window], Any],
    *,
    workers: int | None = None,
    log: Callable[[str], None] | None = None,
    label: str = "分块计算",
    per_worker_mb: int | None = None,
) -> Iterable[tuple[int, Window, Any]]:
    """并行遍历格网块，按完成顺序产出 (块下标, 窗口, 结果)。

    setup 与 fn 必须是模块级可 pickle 的对象（不能是 lambda 或闭包）。
    workers <= 1 时在当前进程直接跑，便于打断点调试。
    per_worker_mb 为单进程内存估计，用于按内存封顶进程数。
    """
    windows = list(windows)
    n = resolve_workers(workers, per_worker_mb=per_worker_mb)
    total = len(windows)
    if not windows:
        return
    if log is not None:
        est = f"，单进程估 {per_worker_mb} MB" if per_worker_mb else ""
        log(f"{label}：{total} 块，{n} 进程{est}")

    # 只有调用方显式要求串行才在本进程跑。内存紧时也走 1 个子进程：
    # 并发的几个波段若都退回本进程，会挤在同一把 GIL 上（实测 8 h 只推进半个波段），
    # 内存也落到不该被杀的主进程上。
    if workers is not None and int(workers) <= 1:
        ctx = setup()
        for k, w in enumerate(windows):
            yield k, w, fn(ctx, w)
            if log is not None and (k + 1) % 10 == 0:
                log(f"{label} {k + 1}/{total} 块")
        return

    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from concurrent.futures.process import BrokenProcessPool

    # 每进程分若干段：缓解块间耗时不均，被杀时丢掉的进度也少
    chunks = spatial_chunks(windows, n * JOB_SEGMENTS_PER_WORKER)
    jobs = [TileJob(c, [windows[k] for k in c]) for c in chunks]
    done = 0
    ctx = multiprocessing.get_context("spawn")
    remaining = list(jobs)
    n_now = n
    peak_mb = 0.0
    broken_at_one = 0
    per_mb = int(per_worker_mb or TILE_WORKER_MB)
    while remaining:
        finished: list[int] = []
        pool = None
        try:
            with ProcessPoolExecutor(
                max_workers=n_now,
                mp_context=ctx,
                initializer=_init_worker,
                initargs=(setup,),
            ) as pool, reserve_memory(
                n_now * per_mb, lambda: _pids_rss_mb(list((pool._processes or {}).keys()))
            ):
                futs = {pool.submit(_run_job, fn, job): i for i, job in enumerate(remaining)}
                for fut in as_completed(futs):
                    idx = futs[fut]
                    results, job_peak = fut.result()
                    peak_mb = max(peak_mb, job_peak)
                    for k, result in results:
                        yield k, windows[k], result
                        done += 1
                        if log is not None and (done == total or done % 10 == 0):
                            log(f"{label} {done}/{total} 块")
                    finished.append(idx)
            if log is not None and peak_mb > 0:
                log(f"{label} 子进程实测峰值 {peak_mb:.0f} MB")
            return
        except BrokenProcessPool:
            remaining = [job for i, job in enumerate(remaining) if i not in finished]
            # 只清本池残留。按任务主进程清会连带杀掉其它波段健康的池（曾一杀三断）。
            _kill_pool(pool)
            if not remaining:
                return
            if n_now == 1:
                broken_at_one += 1
                if broken_at_one >= MAX_BROKEN_AT_ONE:
                    raise MemoryError(
                        f"{label}：单个计算进程连续 {broken_at_one} 次被系统回收，"
                        f"剩余内存不足以跑一个约 {per_mb} MB 的进程"
                    ) from None
            n_now = min(max(1, n_now // 2), resolve_workers(n_now, per_worker_mb=per_mb))
            if log is not None:
                log(f"{label} 计算进程被系统回收，改为 {n_now} 进程续跑剩余 {len(remaining)} 段")
