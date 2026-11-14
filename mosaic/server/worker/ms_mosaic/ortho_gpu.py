"""真正射逐块重采样的 GPU 实现，与 ortho.orthorectify_tile 同签名、同算法。

投影、Z-buffer、双线性采样、视角质量分搬到显卡；选视角和邻域高差仍在 CPU（每块只算一次，量小）。
坐标与投影中间量全程 float64：UTM 坐标六七位整数，float32 只剩厘米级，取整到像素时会翻转。
device=None 时同一份代码走 numpy，用来和 CPU 版逐像元对拍。
Z-buffer 每像素取最近距离：CPU 用排序取首个，GPU 用 scatter_min；取最小与顺序无关，结果相同。
"""

from __future__ import annotations

from collections import OrderedDict

import numpy as np

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.grid import Grid
from ms_mosaic.ortho import (
    NativeImageCache,
    OrthoConfig,
    WarpedStack,
    _merge_min_depth,
    neighbor_drop,
    select_views,
)
from ms_mosaic.xp import array_module, asnumpy, on_device

DEVICE_IMAGE_SLOTS = 12


def _einsum_rows(xp, d, rotation):
    if xp is np:
        return np.einsum("ij,kj->ik", d, rotation, optimize=True)
    # 3×3 旋转逐元素展开，不走 cuBLAS（cupy-core 不带数学库）
    return xp.stack(
        [d[:, 0] * rotation[k, 0] + d[:, 1] * rotation[k, 1] + d[:, 2] * rotation[k, 2] for k in range(3)],
        axis=1,
    )


def _project(xp, cam: Camera, rotation, center, pts):
    """camera.project 的 xp 版，逐式相同。"""
    pc = _einsum_rows(xp, pts - center, rotation)
    z = pc[:, 2]
    valid = z > 1e-6
    safe_z = xp.where(valid, z, 1.0)
    xn = pc[:, 0] / safe_z
    yn = pc[:, 1] / safe_z
    limit = cam.format_normalized_radius()
    valid = valid & ((xn * xn + yn * yn) <= limit * limit)
    u, v = cam.project_normalized(xn, yn)
    return u, v, valid


def _in_bounds(cam: Camera, u, v, margin: float):
    return (u >= margin) & (u <= cam.width - 1 - margin) & (v >= margin) & (v <= cam.height - 1 - margin)


def _sample_bilinear(xp, image, u, v):
    """dense._sample_bilinear 的 xp 版，逐式相同。"""
    h, w = image.shape
    finite = xp.isfinite(u) & xp.isfinite(v)
    us = xp.where(finite, u, 0.0)
    vs = xp.where(finite, v, 0.0)
    ok = finite & (us >= 0.0) & (us <= w - 1) & (vs >= 0.0) & (vs <= h - 1)
    u0 = xp.floor(us).astype(xp.int32)
    v0 = xp.floor(vs).astype(xp.int32)
    u0c = xp.clip(u0, 0, w - 2)
    v0c = xp.clip(v0, 0, h - 2)
    du = (us - u0c).astype(xp.float32)
    dv = (vs - v0c).astype(xp.float32)
    i00 = image[v0c, u0c]
    i10 = image[v0c, u0c + 1]
    i01 = image[v0c + 1, u0c]
    i11 = image[v0c + 1, u0c + 1]
    top = i00 * (1 - du) + i10 * du
    bot = i01 * (1 - du) + i11 * du
    out = top * (1 - dv) + bot * dv
    return xp.where(ok, out, xp.nan)


def _view_score(xp, cam: Camera, center, pts, u, v):
    los = pts - center
    dist = xp.linalg.norm(los, axis=1)
    cos_inc = xp.clip(-los[:, 2] / xp.maximum(dist, 1e-9), 0.0, 1.0)
    du = (u - cam.cx) / (0.5 * cam.width)
    dv = (v - cam.cy) / (0.5 * cam.height)
    radial = xp.sqrt(du * du + dv * dv)
    center_weight = xp.clip(1.0 - 0.5 * radial, 0.0, 1.0)
    return (cos_inc**2) * center_weight


def _merge_min(xp, zbuf, flat, rng) -> None:
    if flat.size == 0:
        return
    if xp is np:
        _merge_min_depth(zbuf, flat, rng)
        return
    import cupyx

    cupyx.scatter_min(zbuf, flat, rng)


def _facade_chunks(xp, pts, drop_m, *, gsd: float, max_sub: int, min_drop_m: float, chunk_points: int):
    """ortho._facade_sample_chunks 的 xp 版。"""
    tall = drop_m > min_drop_m
    if not bool(tall.any()):
        return
    base = pts[tall]
    depth = drop_m[tall]
    n_sub = int(np.clip(np.ceil(float(depth.max()) / max(gsd, 1e-6)), 1, max_sub))
    per = max(1, int(chunk_points) // max(1, base.shape[0]))
    for s in range(1, n_sub + 1, per):
        fracs = (xp.arange(s, min(n_sub, s + per - 1) + 1) / n_sub)[:, None]
        extra = xp.repeat(base[None, :, :], fracs.shape[0], axis=0)
        extra[:, :, 2] -= fracs * depth[None, :]
        yield extra.reshape(-1, 3)


def _visibility(xp, cam: Camera, rotation, center, pts, u, v, inside, drop, *, cfg: OrthoConfig, gsd: float,
                chunk_points: int):
    """ortho.visibility_zbuffer 的 xp 版。"""
    visible = xp.zeros(pts.shape[0], bool)
    sel = xp.nonzero(inside)[0]
    main = pts[sel]
    rng = xp.linalg.norm(main - center, axis=1)
    cols = xp.clip(xp.round(u[sel]).astype(xp.int64), 0, cam.width - 1)
    rows = xp.clip(xp.round(v[sel]).astype(xp.int64), 0, cam.height - 1)
    flat = rows * cam.width + cols
    zbuf = xp.full(cam.width * cam.height, xp.inf, xp.float64)
    _merge_min(xp, zbuf, flat, rng)
    for extra in _facade_chunks(
        xp, main, drop[sel], gsd=gsd, max_sub=cfg.facade_max_samples, min_drop_m=0.5, chunk_points=chunk_points
    ):
        eu, ev, eok = _project(xp, cam, rotation, center, extra)
        keep = eok & _in_bounds(cam, eu, ev, 0)
        if bool(keep.any()):
            ec = xp.clip(xp.round(eu[keep]).astype(xp.int64), 0, cam.width - 1)
            er = xp.clip(xp.round(ev[keep]).astype(xp.int64), 0, cam.height - 1)
            _merge_min(xp, zbuf, er * cam.width + ec, xp.linalg.norm(extra[keep] - center, axis=1))
        del extra, eu, ev, eok, keep
    visible[sel] = rng <= zbuf[flat] + cfg.occlusion_tolerance_m
    del zbuf
    return visible


def _device_image(xp, images, idx: int):
    if xp is np:
        return images[idx]
    cache = getattr(images, "_device_images", None)
    if cache is None:
        cache = OrderedDict()
        images._device_images = cache
    hit = cache.get(idx)
    if hit is not None:
        cache.move_to_end(idx)
        return hit
    arr = xp.asarray(images[idx])
    cache[idx] = arr
    while len(cache) > DEVICE_IMAGE_SLOTS:
        cache.popitem(last=False)
    return arr


def drop_device_images(images) -> None:
    cache = getattr(images, "_device_images", None)
    if cache is not None:
        cache.clear()


def orthorectify_tile(
    grid: Grid,
    window: tuple[int, int, int, int],
    z: np.ndarray,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    images: NativeImageCache,
    cfg: OrthoConfig | None = None,
    *,
    device: int | None = None,
    chunk_points: int | None = None,
) -> WarpedStack:
    """与 ortho.orthorectify_tile 相同的结果；返回的数组都在主机内存。"""
    from ms_mosaic import ortho

    cfg = cfg or OrthoConfig()
    if chunk_points is None:
        chunk_points = ortho.FACADE_CHUNK_POINTS
    row0, col0, rows, cols = window
    xs, ys = grid.cell_centers(window)
    has_z = np.isfinite(z)
    z_mid = float(np.nanmedian(z)) if has_z.any() else 0.0

    views = select_views(grid, window, z_mid, cameras, poses, cfg)
    if not views or not has_z.any():
        return WarpedStack(window, [], np.zeros((0, 1, rows, cols), np.float32),
                           np.full((0, rows, cols), np.nan, np.float32))

    pts_h = np.stack([xs.ravel(), ys.ravel(), np.where(has_z, z, z_mid).ravel()], axis=1)
    drop_h = neighbor_drop(np.where(has_z, z, np.nan)).ravel()
    n_bands = images[views[0]].shape[0]

    xp = array_module(device)
    with on_device(device):
        pts = xp.asarray(pts_h)
        drop = xp.asarray(drop_h)
        has_z_flat = xp.asarray(has_z.ravel())
        pixels = xp.full((len(views), n_bands, rows * cols), xp.nan, xp.float32)
        scores = xp.full((len(views), rows * cols), xp.nan, xp.float32)

        for k, idx in enumerate(views):
            cam, pose = cameras[idx], poses[idx]
            rotation = xp.asarray(pose.rotation)
            center = xp.asarray(pose.center)
            u, v, ok = _project(xp, cam, rotation, center, pts)
            inside = ok & _in_bounds(cam, u, v, cfg.edge_margin_px) & has_z_flat
            if not bool(inside.any()):
                continue
            seen = _visibility(
                xp, cam, rotation, center, pts, u, v, inside, drop,
                cfg=cfg, gsd=grid.gsd, chunk_points=chunk_points,
            )
            if not bool(seen.any()):
                continue
            img = _device_image(xp, images, idx)
            sel = xp.nonzero(seen)[0]
            us, vs = u[sel], v[sel]
            for b in range(n_bands):
                pixels[k, b, sel] = _sample_bilinear(xp, img[b], us, vs)
            s = _view_score(xp, cam, center, pts[sel], us, vs)
            good = xp.isfinite(pixels[k][:, sel]).all(axis=0)
            scores[k, sel] = xp.where(good, s, xp.nan)

        pixels_h = asnumpy(pixels)
        scores_h = asnumpy(scores)
        del pixels, scores, pts, drop, has_z_flat

    return WarpedStack(
        window,
        views,
        pixels_h.reshape(len(views), n_bands, rows, cols),
        scores_h.reshape(len(views), rows, cols),
    )


def is_out_of_memory(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name == "OutOfMemoryError":
        return True
    return name == "CUDARuntimeError" and "memory" in str(exc).lower()


def orthorectify_tile_on(
    device: int | None,
    grid: Grid,
    window: tuple[int, int, int, int],
    z: np.ndarray,
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    images: NativeImageCache,
    cfg: OrthoConfig | None = None,
    *,
    log=None,
) -> WarpedStack:
    """优先 GPU；显存不足时立面分批减半重试，减到最小仍不足则这一块用 CPU。"""
    from ms_mosaic import ortho

    if device is None:
        return ortho.orthorectify_tile(grid, window, z, cameras, poses, images, cfg)
    chunk = ortho.FACADE_CHUNK_POINTS
    while True:
        try:
            return orthorectify_tile(grid, window, z, cameras, poses, images, cfg, device=device, chunk_points=chunk)
        except Exception as exc:  # noqa: BLE001
            if not is_out_of_memory(exc):
                raise
            _free_pool(device)
            drop_device_images(images)
            if chunk <= 1:
                break
            chunk = max(1, chunk // 2)
    if log is not None:
        log(f"正射块 {window}：GPU {device} 显存不足，这一块改用 CPU")
    return ortho.orthorectify_tile(grid, window, z, cameras, poses, images, cfg)


def _free_pool(device: int) -> None:
    import cupy

    with cupy.cuda.Device(device):
        cupy.get_default_memory_pool().free_all_blocks()


def limit_device_memory(device: int, budget_bytes: int) -> None:
    """把本进程在这张卡上的 CuPy 内存池封顶。超出时 CuPy 抛 OutOfMemoryError，由上层分批或退回 CPU。"""
    import cupy

    with cupy.cuda.Device(device):
        cupy.get_default_memory_pool().set_limit(size=int(budget_bytes))
