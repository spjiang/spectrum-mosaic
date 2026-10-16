"""正射块：真正射 → 图割拼接线 → 曝光补偿 → 多频段融合。

这是交付链路里「出图」的核心一步。块之间独立，可按格网并行；主进程负责
把块粘回整幅并写盘，避免多进程同时写同一 GeoTIFF。
"""

from __future__ import annotations

import os
import tempfile
from functools import partial
from pathlib import Path

import numpy as np

from ms_mosaic.blend import (
    OverlapStats,
    accumulate_overlap,
    apply_gains,
    merge_overlap,
    multiband_blend,
    solve_gains,
)
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.dsm import resample_height
from ms_mosaic.grid import Grid, erode_coverage, refine_coverage_mask
from ms_mosaic.ortho import NativeImageCache, OrthoConfig, orthorectify_tile
from ms_mosaic.parallel import CACHE_PER_WORKER, map_tiles, tile_worker_mb
from ms_mosaic.products import (
    RGB_BAND,
    RasterWriter,
    band_dtype,
    group_name,
    quantize,
    rgb_with_alpha,
)
from ms_mosaic.seamline import SeamConfig, optimal_labels

ORTHO_TILE = 384
# 块与块之间必须有足够重叠，并且按权重融合。旧写法 overlap=16 再硬裁内芯，
# 每块独立解增益、独立选拼接线，相邻 384 格亮度可差 70，图上就是左侧那些方块。
ORTHO_OVERLAP = 48


def _ortho_setup(payload: dict):
    ctx = dict(payload)
    path = payload.get("dsm_z_path")
    if path:
        ctx["dsm_z"] = np.load(path, mmap_mode="r")
    ctx["images"] = NativeImageCache(payload["paths"], limit=CACHE_PER_WORKER)
    return ctx


def _share_array(arr: np.ndarray) -> Path:
    """子进程 mmap 同一份数组，避免 spawn 把 DSM 复制 12 份。"""
    fd, name = tempfile.mkstemp(prefix="ms_dsm_", suffix=".npy")
    os.close(fd)
    path = Path(name)
    np.save(path, arr)
    return path


def _feather_weight(
    window: tuple[int, int, int, int], overlap: int, grid: Grid
) -> np.ndarray:
    """窗口内的拼接权重：整幅边缘为 1，与邻块重叠处线性过渡到 0。

    相邻两块的窗口共享 2*overlap 列/行。权重在共享带上从 1 降到 0，
    贴回去时做加权平均，块界就不会留下方块。
    """
    row0, col0, rows, cols = window
    wr = np.ones((rows, cols), np.float32)
    if overlap <= 0:
        return wr
    rr = np.arange(rows, dtype=np.float32)[:, None]
    cc = np.arange(cols, dtype=np.float32)[None, :]
    dist = np.full((rows, cols), np.float32(2 * overlap), np.float32)
    if row0 > 0:
        dist = np.minimum(dist, rr)
    if col0 > 0:
        dist = np.minimum(dist, cc)
    if row0 + rows < grid.height:
        dist = np.minimum(dist, rows - rr)
    if col0 + cols < grid.width:
        dist = np.minimum(dist, cols - cc)
    return np.clip(dist / float(2 * overlap), 0.0, 1.0).astype(np.float32)


def _stack_for_window(ctx: dict, window: tuple[int, int, int, int]):
    z = resample_height(ctx["dsm_z"], ctx["dsm_grid"], ctx["grid"], window)
    return orthorectify_tile(
        ctx["grid"], window, z, ctx["cameras"], ctx["poses"], ctx["images"], ctx["ortho_cfg"]
    )


def _overlap_fn(ctx: dict, window: tuple[int, int, int, int]):
    """第一遍：只统计重叠亮度，不融合。增益必须等全图汇总后再解。"""
    stack = _stack_for_window(ctx, window)
    if stack.pixels.shape[0] == 0:
        return OverlapStats()
    acc = OverlapStats()
    accumulate_overlap(stack, acc, channel=None)
    return acc


def _ortho_fn(ctx: dict, window: tuple[int, int, int, int]):
    stack = _stack_for_window(ctx, window)
    gains = ctx.get("gains") or {}
    if gains:
        stack = apply_gains(stack, gains)
    labels = optimal_labels(stack, ctx["seam_cfg"])
    mosaic = multiband_blend(stack, labels)
    return mosaic, labels, list(stack.views)


def render_band(
    grid: Grid,
    dsm_z: np.ndarray,
    dsm_grid: Grid,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    paths: dict[int, Path],
    *,
    band: str,
    tile: int = ORTHO_TILE,
    overlap: int = ORTHO_OVERLAP,
    workers: int | None = None,
    gain: bool | None = None,
    log=None,
    scratch_root: Path | str | None = None,
) -> BandRaster:
    """整幅正射（浮点，落盘累加）+ 拼接线标号。标号是块内视角下标，views 是全局影像 id。

    RGB 默认先全图统计重叠再解 Brown & Lowe 全局增益（禁止按块），然后融合。
    多光谱不加增益，与「颜色校正：禁用」一致。调用方用完须 close()。
    """
    use_gain = (band == RGB_BAND) if gain is None else bool(gain)
    windows = list(grid.tiles(tile, overlap=overlap))
    any_cam = next(iter(cameras.values()))
    # 影像缓存保持原始位深：RGB 3×uint8，多光谱 1×uint16
    image_bytes = any_cam.width * any_cam.height * (3 if band == RGB_BAND else 2)
    shared = _share_array(np.asarray(dsm_z))
    payload = {
        "grid": grid,
        "dsm_z_path": str(shared),
        "dsm_grid": dsm_grid,
        "cameras": cameras,
        "poses": poses,
        "paths": paths,
        "ortho_cfg": OrthoConfig(),
        "seam_cfg": SeamConfig(),
        "gains": {},
    }
    try:
        return _render_band_with_payload(
            grid,
            payload,
            windows,
            band=band,
            overlap=overlap,
            workers=workers,
            use_gain=use_gain,
            log=log,
            per_worker_mb=tile_worker_mb(image_bytes),
            scratch_root=scratch_root,
        )
    finally:
        try:
            shared.unlink()
        except OSError:
            pass


def _render_band_with_payload(
    grid: Grid,
    payload: dict,
    windows: list,
    *,
    band: str,
    overlap: int,
    workers: int | None,
    use_gain: bool,
    log,
    per_worker_mb: int | None = None,
    scratch_root: Path | str | None = None,
) -> BandRaster:
    out_name = group_name(band)
    if use_gain:
        acc = OverlapStats()
        for _, _window, tile_acc in map_tiles(
            windows,
            partial(_ortho_setup, payload),
            _overlap_fn,
            workers=workers,
            log=log,
            label=f"曝光统计 {out_name} ({band})",
            per_worker_mb=per_worker_mb,
        ):
            if tile_acc:
                merge_overlap(acc, tile_acc)
        payload["gains"] = solve_gains(acc)
        if log is not None:
            vals = list(payload["gains"].values())
            if vals:
                p = np.percentile(vals, [10, 50, 90])
                log(
                    f"全局增益 {out_name} ({band}) {len(vals)} 张，"
                    f"p10/p50/p90={p[0]:.3f}/{p[1]:.3f}/{p[2]:.3f}"
                )
            else:
                log(f"全局增益 {out_name} ({band})：重叠不足，保持 1.0")
    sample_bands = 3 if band == RGB_BAND else 1
    raster = BandRaster(grid, sample_bands, scratch_root=scratch_root)
    try:
        for _, window, (tile_m, tile_l, views) in map_tiles(
            windows,
            partial(_ortho_setup, payload),
            _ortho_fn,
            workers=workers,
            log=log,
            label=f"正射 {out_name} ({band})",
            per_worker_mb=per_worker_mb,
        ):
            if tile_m.size == 0:
                continue
            raster.add_tile(window, tile_m, tile_l, views, _feather_weight(window, overlap, grid))
    except BaseException:
        raster.close()
        raise
    raster.flush()
    return raster


class BandRaster:
    """整幅正射的加权累加器，落在本地磁盘的 memmap 上。

    2.1 亿像元的 Color 正射，浮点累加 + 权重 + 标号常驻内存约 4.7 GB，最后归一化
    再叠 3 份整幅副本，峰值 13 GB，在 24 GB 引擎里被系统杀掉。这里块结果直接
    累加进磁盘页（内核可随时回写换出），出图按条带取，主进程不驻留整幅数组。
    """

    def __init__(self, grid: Grid, n_bands: int, *, scratch_root: Path | str | None = None) -> None:
        from numpy.lib.format import open_memmap

        root = Path(scratch_root) if scratch_root is not None else scratch_dir()
        root.mkdir(parents=True, exist_ok=True)
        _sweep_stale_scratch(root)
        self.dir = Path(tempfile.mkdtemp(prefix=f"{os.getpid()}_", dir=root))
        self.grid = grid
        h, w = grid.height, grid.width
        # w+ 新建文件为稀疏零页，不用显式清零
        self.acc = open_memmap(self.dir / "acc.npy", mode="w+", dtype=np.float32, shape=(n_bands, h, w))
        self.wsum = open_memmap(self.dir / "wsum.npy", mode="w+", dtype=np.float32, shape=(h, w))
        # 标号存 +1（0 表示无），免去整幅写 -1
        self._labels1 = open_memmap(self.dir / "labels.npy", mode="w+", dtype=np.int16, shape=(h, w))
        self.label_w = open_memmap(self.dir / "label_w.npy", mode="w+", dtype=np.float32, shape=(h, w))
        self.views: list[int] = []
        self._view_set: set[int] = set()

    @property
    def n_bands(self) -> int:
        return int(self.acc.shape[0])

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(self.acc.shape)

    def add_tile(self, window, tile_m: np.ndarray, tile_l: np.ndarray, views, wr: np.ndarray) -> None:
        if tile_m.shape[0] != self.n_bands:
            if tile_m.shape[0] == 1 and self.n_bands == 3:
                tile_m = np.repeat(tile_m, 3, axis=0)
            else:
                return
        row0, col0, rows, cols = window
        finite = np.isfinite(tile_m).all(axis=0)
        w = np.where(finite, wr, 0.0).astype(np.float32)
        sl = (slice(row0, row0 + rows), slice(col0, col0 + cols))
        self.acc[(slice(None),) + sl] += np.nan_to_num(tile_m, nan=0.0) * w
        self.wsum[sl] += w
        lw = np.asarray(self.label_w[sl])
        better = w > lw
        self._labels1[sl] = np.where(better, tile_l.astype(np.int16) + 1, self._labels1[sl])
        self.label_w[sl] = np.where(better, w, lw)
        for v in views:
            if v not in self._view_set:
                self._view_set.add(v)
                self.views.append(v)

    def strip(self, r0: int, r1: int) -> np.ndarray:
        """第 r0:r1 行的浮点融合结果，无贡献处为 nan。"""
        acc = np.asarray(self.acc[:, r0:r1], np.float32)
        wsum = np.asarray(self.wsum[r0:r1], np.float32)
        good = wsum > 1e-6
        return np.where(good, acc / np.maximum(wsum, 1e-6), np.float32(np.nan)).astype(np.float32, copy=False)

    def labels_strip(self, r0: int, r1: int) -> np.ndarray:
        return np.asarray(self._labels1[r0:r1], np.int16) - 1

    @property
    def labels(self) -> np.ndarray:
        """整幅拼接线标号（块内视角下标，-1 为无）。仅拼接线导出用，会整幅读入。"""
        return np.asarray(self._labels1, np.int16) - 1

    def to_array(self) -> np.ndarray:
        """整幅浮点结果。只给验收套色等调试路径用，主路径不要调。"""
        return self.strip(0, self.grid.height)

    def flush(self) -> None:
        for arr in (self.acc, self.wsum, self._labels1, self.label_w):
            arr.flush()

    def close(self) -> None:
        import shutil

        for name in ("acc", "wsum", "_labels1", "label_w"):
            arr = getattr(self, name, None)
            mm = getattr(arr, "_mmap", None)
            if mm is not None:
                try:
                    mm.close()
                except (BufferError, ValueError):
                    pass
            setattr(self, name, None)
        shutil.rmtree(self.dir, ignore_errors=True)


def scratch_dir() -> Path:
    """正射累加器的落盘目录。默认容器本地 /tmp（ext4），不要放到宿主机共享卷上做 mmap。"""
    return Path(os.environ.get("MS_SCRATCH_DIR") or tempfile.gettempdir()) / "ms_ortho"


def _sweep_stale_scratch(root: Path) -> None:
    """清掉已退出进程留下的累加器目录（任务被杀时来不及删）。"""
    import shutil

    for entry in root.iterdir():
        pid_s = entry.name.split("_", 1)[0]
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            shutil.rmtree(entry, ignore_errors=True)
        except PermissionError:
            pass


class _ArraySource:
    """把内存里的整幅数组包成按条带读取的接口。"""

    def __init__(self, mosaic: np.ndarray) -> None:
        self._m = np.asarray(mosaic)
        self.shape = self._m.shape

    def strip(self, r0: int, r1: int) -> np.ndarray:
        return self._m[:, r0:r1]


STRIP_ROWS = 512
INPAINT_MARGIN_ROWS = 64


def write_band_product(
    mosaic,
    grid: Grid,
    band: str,
    path: Path,
    *,
    coverage: np.ndarray | None = None,
    trim_m: float = 0.0,
    strip_rows: int = STRIP_ROWS,
) -> Path:
    """量化、覆盖掩膜与补洞后按条带写出 GeoTIFF。

    规则与 apply_coverage_mask 相同：先由「有效且（RGB）非全 0」得到已知掩膜，
    去游离斑、填内部孔、按需收边得到 take；take 内未知像元取最近已知像元。
    全图只常驻两张 1 B/像元的布尔掩膜，量化与补洞都在条带里做。
    """
    from scipy.ndimage import binary_fill_holes

    src = mosaic if hasattr(mosaic, "strip") else _ArraySource(mosaic)
    h, w = src.shape[-2:]
    step = max(1, int(strip_rows))

    def quant(r0: int, r1: int) -> tuple[np.ndarray, np.ndarray]:
        q, valid = quantize(src.strip(r0, r1), band)
        if coverage is not None:
            valid = valid & np.asarray(coverage[r0:r1], bool)
        if q.shape[0] >= 3:
            valid = valid & (q[:3] > 0).any(axis=0)
        return q, valid

    known = np.zeros((h, w), bool)
    for r0 in range(0, h, step):
        r1 = min(h, r0 + step)
        known[r0:r1] = quant(r0, r1)[1]
    take = refine_coverage_mask(known, max_hole_cells=256, merge_gap_cells=8)
    take = binary_fill_holes(take)
    if trim_m and float(trim_m) > 0:
        take = erode_coverage(take, grid.gsd, float(trim_m))

    rgb = band == RGB_BAND
    count, dtype = (4, "uint8") if rgb else (1, band_dtype(band))
    with RasterWriter(path, grid, count=count, dtype=dtype, alpha=rgb) as wr:
        for r0 in range(0, h, step):
            r1 = min(h, r0 + step)
            out = _inpaint_strip(quant, known, take, r0, r1)
            out[:, ~take[r0:r1]] = 0
            data = rgb_with_alpha(out, take[r0:r1]) if rgb else out[:1]
            wr.write((r0, 0, r1 - r0, w), data)
    return Path(path)


def _inpaint_strip(quant, known: np.ndarray, take: np.ndarray, r0: int, r1: int) -> np.ndarray:
    """条带内 take 且未知的像元取最近已知像元（与整幅 inpaint_nearest 同规则）。

    在条带上下各扩 margin 行的窗口里做距离变换。窗口外的已知像元离第 r 行至少
    min(r−w0+1, w1−r)，找到的距离不超过它就一定是全局最近；否则加倍 margin 重算。
    """
    from scipy.ndimage import distance_transform_edt

    h = known.shape[0]
    need = take[r0:r1] & ~known[r0:r1]
    if not need.any():
        return quant(r0, r1)[0].copy()
    rows = np.arange(r0, r1)
    margin = INPAINT_MARGIN_ROWS
    while True:
        w0, w1 = max(0, r0 - margin), min(h, r1 + margin)
        full = w0 == 0 and w1 == h
        kw = known[w0:w1]
        if not kw.any():
            if full:
                return quant(r0, r1)[0].copy()
            margin *= 2
            continue
        dist, idx = distance_transform_edt(~kw, return_indices=True)
        d = dist[r0 - w0 : r1 - w0]
        top = (rows - w0 + 1).astype(np.float64) if w0 > 0 else np.full(rows.size, np.inf)
        bottom = (w1 - rows).astype(np.float64) if w1 < h else np.full(rows.size, np.inf)
        bound = np.broadcast_to(np.minimum(top, bottom)[:, None], d.shape)
        if full or bool(np.all(d[need] <= bound[need])):
            break
        margin *= 2
    qw, _ = quant(w0, w1)
    out = qw[:, r0 - w0 : r1 - w0].copy()
    rr = idx[0][r0 - w0 : r1 - w0][need]
    cc = idx[1][r0 - w0 : r1 - w0][need]
    out[:, need] = qw[:, rr, cc]
    return out
