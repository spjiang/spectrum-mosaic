"""质量报告用的统计与附图。数字全部来自本次解算，版式对齐 LiMapper 4.0。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from ms_mosaic.camera import Camera, Pose, wrap_yaw_deg, ypr_from_rotation
from ms_mosaic.grid import Grid
from ms_mosaic.matching import PairMatches
from ms_mosaic.scene import PRIMARY_BAND, Block, transfer_band

GPS_BIN_EDGES = tuple(range(-6, 20, 2))
YAW_TURN_DEG = 40.0
# 略放宽间距阈值，减少把同航迹首尾拆成单张航线（商业 MAX_0001–0005 同线）
GAP_STEP_FACTOR = 5.0
POOR_FORWARD = 0.60
POOR_SIDE = 0.30
POOR_KEYPOINT_FRACTION = 0.20
MIN_POOR_KEYPOINTS = 80


def observation_summary(at) -> tuple[dict[int, int], dict[int, float]]:
    """每张影像的 (观测数, 残差平方和)。逐条观测已释放时读 ATResult 上的摘要。"""
    counts = dict(getattr(at, "obs_counts", None) or {})
    sq = dict(getattr(at, "obs_sq_residual", None) or {})
    if counts:
        return counts, sq
    observations = list(getattr(at, "observations", None) or [])
    if not observations:
        return {}, {}
    raw = getattr(at, "residuals_px", None)
    residuals = np.asarray([] if raw is None else raw, float).ravel()
    with_res = residuals.size == len(observations)
    for k, obs in enumerate(observations):
        idx = int(obs.image)
        counts[idx] = counts.get(idx, 0) + 1
        if with_res:
            sq[idx] = sq.get(idx, 0.0) + float(residuals[k]) ** 2
    return counts, sq


def registered_image_ids(at, tracks=None) -> set[int]:
    """已注册影像：有空三观测；检查点无观测时读 stats / 连接点。"""
    obs_counts, _ = observation_summary(at)
    if obs_counts:
        return {i for i, n in obs_counts.items() if n > 0}
    saved = (getattr(at, "stats", None) or {}).get("registered_ids")
    if saved:
        return {int(i) for i in saved}
    if tracks is not None:
        counts = tracks.per_image_counts()
        hit = {int(i) for i, n in counts.items() if n > 0}
        if hit:
            return hit
    return {int(i) for i in getattr(at, "poses", {}) or {}}


def keypoint_series(block: Block, keypoint_counts: dict[int, int]) -> list[tuple[int, int]]:
    """按全部影像建特征点序列；缺省为 0，对齐商业「均值按 1896 张、最少可在窄波段」。"""
    return [(im.index, int(keypoint_counts.get(im.index, 0))) for im in block.images]


def band_rms_px(block: Block, at) -> dict[str, float | None]:
    """各组重投影 RMS；无分观测残差时退回全局。"""
    global_rms = None
    if getattr(at, "stats", None):
        global_rms = at.stats.get("rms_reprojection_px")
    counts, sq = observation_summary(at)
    if not counts or not sq:
        return {band: global_rms for band in block.band_order() if band in block.cameras}
    by_index = {im.index: im for im in block.images}
    band_n: dict[str, int] = defaultdict(int)
    band_sq: dict[str, float] = defaultdict(float)
    for idx, n in counts.items():
        im = by_index.get(int(idx))
        if im is None:
            continue
        band_n[im.band] += int(n)
        band_sq[im.band] += float(sq.get(idx, 0.0))
    out: dict[str, float | None] = {}
    for band in block.band_order():
        if band not in block.cameras:
            continue
        n = band_n.get(band, 0)
        out[band] = float(np.sqrt(band_sq[band] / n)) if n else global_rms
    return out


def crs_label(crs: str) -> str:
    raw = str(crs)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 4:
        return raw
    code = int(digits[-5:]) if len(digits) >= 5 else int(digits)
    zone = code % 100
    hemi = "S" if 32700 <= code < 32800 else "N"
    return f"[EPSG::{code}] UTM {zone}{hemi} (WGS84), egm_none"


def survey_area_km2(z: np.ndarray, grid: Grid) -> float:
    """测区面积：有效 DSM 像元的轴对齐外接矩形，单位平方公里。

    不用整幅格网外框——格网常含大块 nodata，会明显大于真实有图范围。
    LiMapper 报告的测区面积也接近「有内容的外包」，而非空白边框。
    无有效像元时退回整幅格网外框。
    """
    valid = np.isfinite(z)
    if not valid.any():
        left, bottom, right, top = grid.bounds
        return abs(right - left) * abs(top - bottom) / 1e6
    rows = np.where(valid.any(axis=1))[0]
    cols = np.where(valid.any(axis=0))[0]
    r0, r1 = int(rows[0]), int(rows[-1]) + 1
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    # 像元外缘（与 rasterio 窗口一致），不是中心
    x0 = grid.transform.c + c0 * grid.transform.a
    x1 = grid.transform.c + c1 * grid.transform.a
    y0 = grid.transform.f + r0 * grid.transform.e
    y1 = grid.transform.f + r1 * grid.transform.e
    return abs(x1 - x0) * abs(y1 - y0) / 1e6


def mean_flight_agl_m(block: Block, at, dsm) -> float:
    """平均航高（相对地面），单位米。

    优先用照片 XMP ``AboveGroundAltitude``：编目时
    ``block.ground_z = 平均绝对高程 − 平均相对航高``，故
    ``平均绝对高程 − ground_z`` 即各曝光相对航高的均值。

    若照片没有相对航高，再退回「空三相机高 − DSM 中位高程」。
    不要用有偏的 DSM 去减椭球相机高——本测区 DSM 中位偏低约 10 m 时，
    会把航高从约 70 m 抬到 84 m。
    """
    primary = block.primary()
    alts = [
        float(block.gps[im.index][2])
        for im in primary
        if im.index in block.gps
    ]
    if alts and abs(block.ground_z) > 1e-6:
        xmp_agl = float(np.mean(alts) - block.ground_z)
        if xmp_agl > 1.0:
            return xmp_agl
    z_med = float(dsm.stats.get("z_median", block.ground_z))
    centers = [at.poses[i].center[2] for i in at.poses]
    if not centers:
        return 0.0
    return float(sum(abs(z - z_med) for z in centers) / len(centers))


def chinese_date(when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{when.year}年{when.month}月{when.day}日"


def ctime_en(when: datetime | None = None) -> str:
    when = when or datetime.now()
    return when.strftime("%a %b %d %H:%M:%S %Y")


def gps_histogram(rows: Sequence[dict], edges: Sequence[int] = GPS_BIN_EDGES) -> list[dict]:
    """与 LiMapper 相同的 2 m 分箱，值为百分比。"""
    edges = list(edges)
    n = max(1, len(rows))
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        rec = {"label": f"[{lo} ~ {hi})", "lo": lo, "hi": hi}
        for key, src in (("error", "error"), ("dx", "dx"), ("dy", "dy"), ("dz", "dz")):
            vals = np.array([r[src] for r in rows], float)
            rec[key] = float(np.mean((vals >= lo) & (vals < hi)) * 100.0) if rows else 0.0
        out.append(rec)
    for key in ("error", "dx", "dy", "dz"):
        if not rows:
            continue
        vals = np.array([r[key] for r in rows], float)
        lo, hi = edges[0], edges[-1]
        out[0][key] += float(np.mean(vals < lo) * 100.0)
        out[-1][key] += float(np.mean(vals >= hi) * 100.0)
    return out


def gps_extrema(rows: Sequence[dict]) -> dict[str, dict]:
    if not rows:
        return {}

    def pick(key: str, largest: bool) -> dict:
        if key == "error":
            scored = [(abs(r["error"]), r) for r in rows]
        else:
            scored = [(abs(r[key]), r) for r in rows]
        scored.sort(key=lambda t: t[0], reverse=largest)
        return dict(scored[0][1])

    return {
        "min_x": pick("dx", False),
        "min_y": pick("dy", False),
        "min_z": pick("dz", False),
        "min_err": pick("error", False),
        "max_x": pick("dx", True),
        "max_y": pick("dy", True),
        "max_z": pick("dz", True),
        "max_err": pick("error", True),
    }


def format_gps_extrema_line(row: dict) -> str:
    g0, g1 = row["gps0"], row["gps1"]
    return (
        f"序号 = {row['index']}, {row['name']} , "
        f"初始GPS({g0[0]:.4f}, {g0[1]:.3f}, {g0[2]:.7f}), "
        f"优化GPS({g1[0]:.4f}, {g1[1]:.3f}, {g1[2]:.7f}), "
        f"距离 = {row['error']:.5g} "
        f"(dx = {row['dx']:.9g}, dy = {row['dy']:.6g}, dz = {row['dz']:.6g})"
    )


def truncate_rows(rows: Sequence, *, head: int = 12, tail: int = 10) -> list:
    if len(rows) <= head + tail + 1:
        return list(rows)
    return list(rows[:head]) + [None] + list(rows[-tail:])


def split_strips(
    indices: Sequence[int],
    centers: np.ndarray,
    yaws: np.ndarray,
    *,
    yaw_turn_deg: float = YAW_TURN_DEG,
    gap_factor: float = GAP_STEP_FACTOR,
) -> list[list[int]]:
    """按航向突变与间距突变切航线。indices 须已按飞行顺序排好。"""
    if not indices:
        return []
    centers = np.asarray(centers, float)
    yaws = np.asarray(yaws, float)
    steps = []
    for k in range(1, len(indices)):
        steps.append(float(np.linalg.norm(centers[k, :2] - centers[k - 1, :2])))
    typical = float(np.median(steps)) if steps else 0.0
    strips = [[int(indices[0])]]
    for k in range(1, len(indices)):
        dyaw = abs(wrap_yaw_deg(float(yaws[k]) - float(yaws[k - 1])))
        dist = steps[k - 1]
        if dyaw > yaw_turn_deg or (typical > 1.0 and dist > gap_factor * typical):
            strips.append([int(indices[k])])
        else:
            strips[-1].append(int(indices[k]))
    return _absorb_singleton_strips(strips, centers, yaws, indices, yaw_turn_deg=yaw_turn_deg)


def _absorb_singleton_strips(
    strips: list[list[int]],
    centers: np.ndarray,
    yaws: np.ndarray,
    indices: Sequence[int],
    *,
    yaw_turn_deg: float,
) -> list[list[int]]:
    """商业航线不把孤立单张单独成线：航向接近时并入前/后一段。"""
    if len(strips) <= 1:
        return strips
    pos = {int(i): k for k, i in enumerate(indices)}
    out = [list(strips[0])]
    for strip in strips[1:]:
        if len(strip) != 1:
            out.append(list(strip))
            continue
        idx = int(strip[0])
        if idx not in pos:
            out.append(list(strip))
            continue
        k = pos[idx]
        merged = False
        if out:
            prev = int(out[-1][-1])
            if prev in pos:
                dyaw = abs(wrap_yaw_deg(float(yaws[k]) - float(yaws[pos[prev]])))
                if dyaw <= yaw_turn_deg:
                    out[-1].append(idx)
                    merged = True
        if not merged:
            out.append([idx])
    # 仍为单张的段尝试并入下一段
    final: list[list[int]] = []
    i = 0
    while i < len(out):
        if len(out[i]) == 1 and i + 1 < len(out):
            idx = int(out[i][0])
            nxt = int(out[i + 1][0])
            if idx in pos and nxt in pos:
                dyaw = abs(wrap_yaw_deg(float(yaws[pos[nxt]]) - float(yaws[pos[idx]])))
                if dyaw <= yaw_turn_deg:
                    final.append([idx] + list(out[i + 1]))
                    i += 2
                    continue
        final.append(list(out[i]))
        i += 1
    return final


def connected_sequences(matches: Iterable[PairMatches], indices: Sequence[int]) -> list[list[int]]:
    parent = {int(i): int(i) for i in indices}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        if a not in parent:
            parent[a] = a
        if b not in parent:
            parent[b] = b
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for pm in matches:
        union(int(pm.i), int(pm.j))
    groups: dict[int, list[int]] = defaultdict(list)
    for i in parent:
        groups[find(i)].append(i)
    seqs = [sorted(v) for v in groups.values()]
    seqs.sort(key=lambda s: (-len(s), s[0]))
    return seqs


def _yaw_of(pose: Pose) -> float:
    return float(ypr_from_rotation(pose.rotation)[0])


def flight_overlap(
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    ground_z: float,
    strips: list[list[int]],
    names: dict[int, str],
) -> dict[str, Any]:
    from ms_mosaic.pairs import footprint_polygons, overlap_ratio

    polys = footprint_polygons(cameras, poses, ground_z)
    forward: list[dict] = []
    for strip in strips:
        for a, b in zip(strip, strip[1:]):
            if a not in polys or b not in polys:
                ratio = 0.0
            else:
                ratio = overlap_ratio(polys[a], polys[b])
            forward.append(
                {"a": a, "b": b, "name_a": names.get(a, str(a)), "name_b": names.get(b, str(b)), "overlap": ratio}
            )
    side: list[dict] = []
    strip_of = {idx: s for s, strip in enumerate(strips) for idx in strip}
    keys = [i for i in poses if i in polys]
    for i in keys:
        best = 0.0
        best_j = None
        yaw_i = _yaw_of(poses[i])
        for j in keys:
            if i >= j or strip_of.get(i) == strip_of.get(j):
                continue
            if abs(wrap_yaw_deg(_yaw_of(poses[j]) - yaw_i)) > 25.0:
                continue
            ratio = overlap_ratio(polys[i], polys[j])
            if ratio > best:
                best, best_j = ratio, j
        if best_j is not None:
            side.append(
                {
                    "a": i,
                    "b": best_j,
                    "name_a": names.get(i, str(i)),
                    "name_b": names.get(best_j, str(best_j)),
                    "overlap": best,
                }
            )
    mean_fwd = float(np.mean([r["overlap"] for r in forward])) if forward else float("nan")
    mean_side = float(np.mean([r["overlap"] for r in side])) if side else float("nan")
    poor_fwd = [r for r in forward if r["overlap"] < POOR_FORWARD]
    poor_side = [r for r in side if r["overlap"] < POOR_SIDE]
    poor_fwd.sort(key=lambda r: r["overlap"])
    poor_side.sort(key=lambda r: r["overlap"])
    return {
        "forward": mean_fwd,
        "side": mean_side,
        "poor_forward": poor_fwd[:20],
        "poor_side": poor_side[:20],
        "n_forward_pairs": len(forward),
        "n_side_pairs": len(side),
    }


def build_gps_rows(block: Block, poses: dict[int, Pose]) -> list[dict]:
    rows = []
    by_index = {im.index: im for im in block.images}
    for idx in sorted(poses):
        if idx not in block.gps:
            continue
        g0 = np.asarray(block.gps[idx], float)
        g1 = np.asarray(poses[idx].center, float)
        d = g1 - g0
        im = by_index.get(idx)
        rows.append(
            {
                "index": idx,
                "name": im.path.name if im is not None else str(idx),
                "group": im.group if im is not None else 0,
                "gps0": [float(g0[0]), float(g0[1]), float(g0[2])],
                "gps1": [float(g1[0]), float(g1[1]), float(g1[2])],
                "dx": float(d[0]),
                "dy": float(d[1]),
                "dz": float(d[2]),
                "error": float(np.linalg.norm(d)),
            }
        )
    return rows


def _image_tag(im, extra: str = "") -> str:
    body = f"序号 = {im.index}, 组号 = {im.group}, {im.path.name}"
    return f"{extra} ({body})" if extra != "" else f"({body})"


def build_quality_payload(
    *,
    input_dir: Path,
    block: Block,
    at,
    tracks,
    matches: Sequence[PairMatches],
    keypoint_counts: dict[int, int],
    dsm,
    dense_stats: dict,
    files: dict,
    timings: dict,
    elapsed_s: float,
    n_scanned: int,
    n_usable: int,
    cameras_info: dict,
    figures: dict[str, str] | None = None,
    color_correction: str = "禁用",
) -> dict[str, Any]:
    by_index = {im.index: im for im in block.images}
    names = {im.index: im.path.name for im in block.images}
    registered = registered_image_ids(at, tracks)
    # 航线/重叠只统计主波段已注册影像（商业航线表仅组 0）
    primary_reg = [im for im in block.primary() if im.index in registered]
    if not primary_reg:
        primary_reg = [im for im in block.primary() if im.index in at.poses]
    strip_ids = [im.index for im in primary_reg if im.index in at.poses]
    centers = (
        np.array([at.poses[i].center for i in strip_ids], float) if strip_ids else np.zeros((0, 3))
    )
    yaws = np.array([_yaw_of(at.poses[i]) for i in strip_ids]) if strip_ids else np.zeros(0)
    strips_idx = split_strips(strip_ids, centers, yaws) if strip_ids else []
    strip_names = [[names[i] for i in strip] for strip in strips_idx]

    primary_cam = at.cameras.get(PRIMARY_BAND) or next(iter(at.cameras.values()), None)
    color_cams = (
        {i: primary_cam for i in strip_ids} if primary_cam is not None else {}
    )
    color_poses = {i: at.poses[i] for i in strip_ids if i in at.poses}
    overlap = flight_overlap(color_cams, color_poses, dsm.stats.get("z_median", block.ground_z), strips_idx, names)

    # 特征统计覆盖全部 1896 张（含窄波段低点数），对齐商业均值/最少/较差影像
    kp_items = keypoint_series(block, keypoint_counts)
    kp_vals = np.array([c for _, c in kp_items], int) if kp_items else np.array([], int)
    mean_kp = float(kp_vals.mean()) if kp_vals.size else 0.0
    max_kp = int(kp_vals.max()) if kp_vals.size else 0
    min_kp = int(kp_vals.min()) if kp_vals.size else 0
    poor_thr = max(MIN_POOR_KEYPOINTS, POOR_KEYPOINT_FRACTION * mean_kp) if mean_kp else MIN_POOR_KEYPOINTS
    poor_kp = []
    for idx, count in kp_items:
        if count < poor_thr and idx in by_index:
            poor_kp.append(_image_tag(by_index[idx], str(count)))

    max_i = int(kp_vals.argmax()) if kp_vals.size else -1
    min_i = int(kp_vals.argmin()) if kp_vals.size else -1
    feat_max = _image_tag(by_index[kp_items[max_i][0]], str(max_kp)) if max_i >= 0 else "-"
    feat_min = _image_tag(by_index[kp_items[min_i][0]], str(min_kp)) if min_i >= 0 else "-"

    pair_count: dict[int, int] = defaultdict(int)
    max_two_view = 0
    for pm in matches:
        n = len(pm)
        max_two_view = max(max_two_view, n)
        pair_count[pm.i] += n
        pair_count[pm.j] += n
    # 连接点/稀疏观测统计覆盖全部波段影像，对齐商业「最多在 850nm、最少在 450nm」
    all_indices = [im.index for im in block.images]
    track_counts = tracks.per_image_counts() if tracks is not None else {}
    if track_counts:
        hi = max(track_counts, key=track_counts.get)
        lo = min(all_indices, key=lambda i: track_counts.get(i, 0)) if all_indices else hi
        match_most = _image_tag(by_index[hi], str(track_counts[hi])) if hi in by_index else str(hi)
        match_least = _image_tag(by_index[lo], str(track_counts.get(lo, 0))) if lo in by_index else str(lo)
        mean_tracks = (
            float(np.mean([track_counts.get(i, 0) for i in all_indices])) if all_indices else 0.0
        )
    else:
        match_most = match_least = "-"
        mean_tracks = 0.0

    obs_per, _ = observation_summary(at)
    if obs_per:
        hi_obs = max(obs_per, key=obs_per.get)
        lo_obs = min(all_indices, key=lambda i: obs_per.get(i, 0)) if all_indices else hi_obs
        sparse_most = _image_tag(by_index[hi_obs], str(obs_per[hi_obs])) if hi_obs in by_index else str(hi_obs)
        sparse_least = _image_tag(by_index[lo_obs], str(obs_per.get(lo_obs, 0))) if lo_obs in by_index else str(lo_obs)
    else:
        sparse_most = sparse_least = "-"

    # 全波段匹配时连通分量按组出现，与商业「关联影像序列」一致
    seq_seed = [im.index for im in block.images]
    sequences = connected_sequences(matches, seq_seed)
    seq_payload = [{"id": k, "indices": seq, "n": len(seq)} for k, seq in enumerate(sequences)]

    # GPS 仅已注册影像（商业参考点 1888 = 已注册数）
    poses_gps: dict[int, Pose] = {i: at.poses[i] for i in registered if i in at.poses}
    for band in block.band_order():
        if band == PRIMARY_BAND:
            continue
        missing = [im.index for im in block.by_band(band) if im.index in registered and im.index not in poses_gps]
        if not missing:
            continue
        _, band_poses, _ = transfer_band(block, at.cameras, at.poses, band)
        for idx in missing:
            if idx in band_poses:
                poses_gps[idx] = band_poses[idx]
    gps_rows = build_gps_rows(block, poses_gps)

    unregistered = sorted(im.index for im in block.images if im.index not in registered)

    area_km2 = survey_area_km2(dsm.z, dsm.grid)
    mean_agl = mean_flight_agl_m(block, at, dsm)
    created = datetime.now()
    n_images = len(block.images)
    n_registered = len(registered)

    at_stats = dict(at.stats)
    at_stats["n_images"] = n_registered
    at_stats["mean_obs_per_image"] = float(
        at_stats.get("n_observations", 0) / max(1, n_registered)
    )
    if gps_rows:
        err = np.array([r["error"] for r in gps_rows])
        dx = np.array([r["dx"] for r in gps_rows])
        dy = np.array([r["dy"] for r in gps_rows])
        dz = np.array([r["dz"] for r in gps_rows])
        at_stats["gps_rmse_m"] = float(np.sqrt(np.mean(err**2)))
        at_stats["gps_rmse_x_m"] = float(np.sqrt(np.mean(dx**2)))
        at_stats["gps_rmse_y_m"] = float(np.sqrt(np.mean(dy**2)))
        at_stats["gps_rmse_z_m"] = float(np.sqrt(np.mean(dz**2)))
        at_stats["n_gps"] = len(gps_rows)

    return {
        "input": str(input_dir),
        "project_name": input_dir.name,
        "created_at": ctime_en(created),
        "created_date_cn": chinese_date(created),
        "n_scanned": n_scanned,
        "n_shots": n_usable,
        "n_images": n_images,
        "n_registered": n_registered,
        "n_filtered": n_scanned - n_usable,
        "crs": block.crs,
        "crs_label": crs_label(block.crs),
        "elapsed_s": round(float(elapsed_s), 3),
        "gcp": 0,
        "area_km2": area_km2,
        "mean_agl_m": mean_agl,
        "files": files,
        "timings": timings,
        "at": at_stats,
        "dense": dense_stats,
        "dsm": dsm.stats,
        "ortho": {
            "gsd": dsm.grid.gsd * 0.5,
            "mode": "基于DSM逐像素拼接",
            "blend": "中",
            "color_correction": color_correction,
            "native_gsd": dense_stats.get("image_gsd"),
            "pyramid_level": dense_stats.get("pyramid_level"),
            "max_tilt_deg": 60,
        },
        "cameras": cameras_info,
        "primary_band": "组0-",
        "features": {
            "n_keypoints": int(kp_vals.sum()) if kp_vals.size else 0,
            "max_keypoints": max_kp,
            "mean_keypoints": mean_kp,
            "min_keypoints": min_kp,
            "scale": "大",
            "most": feat_max,
            "least": feat_min,
            "poor": poor_kp,
            "n_poor": len(poor_kp),
        },
        "matching": {
            "n_tracks": int(len(tracks)) if tracks is not None else at_stats.get("n_points"),
            "pair_mode": "一般",
            "max_two_view": max_two_view,
            "mean_tracks": mean_tracks,
            "most": match_most,
            "least": match_least,
            "sequences": seq_payload,
            "n_matched_pairs": len(matches),
        },
        "sparse_most": sparse_most,
        "sparse_least": sparse_least,
        "unregistered": unregistered,
        "gps_rows": gps_rows,
        "gps_histogram": gps_histogram(gps_rows) if gps_rows else [],
        "gps_extrema": gps_extrema(gps_rows),
        "strips": strip_names,
        "overlap": overlap,
        "figures": figures or {},
        "camera_type": next(iter(at.cameras.values())).model if at.cameras else "MAX-S800",
    }


def _cjk_rcparams() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Heiti SC",
        "Songti SC",
        "Noto Sans CJK SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def _save_fig(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


def thumbnail_ortho(path: Path, out: Path, max_px: int = 520) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.enums import Resampling

    if not path.exists():
        return None
    with rasterio.open(path) as ds:
        scale = max(ds.width, ds.height) / float(max_px)
        h = max(1, int(round(ds.height / max(scale, 1.0))))
        w = max(1, int(round(ds.width / max(scale, 1.0))))
        count = min(3, ds.count)
        data = ds.read(list(range(1, count + 1)), out_shape=(count, h, w), resampling=Resampling.bilinear)
        alpha = None
        if ds.count >= 4:
            alpha = ds.read(4, out_shape=(h, w), resampling=Resampling.nearest)
    img = np.transpose(data, (1, 2, 0))
    if img.dtype != np.uint8:
        finite = np.isfinite(img)
        if finite.any():
            lo = np.nanpercentile(img, 2)
            hi = np.nanpercentile(img, 98)
            img = np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)
        else:
            img = np.zeros_like(img, dtype=float)
    else:
        img = img.astype(float) / 255.0
    if img.shape[2] == 1:
        img = np.repeat(img, 3, axis=2)
    if alpha is not None:
        img = np.dstack([img, np.clip(alpha.astype(float) / 255.0, 0, 1)])
    _cjk_rcparams()
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    ax.imshow(img)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
    return _save_fig(fig, out)


def thumbnail_dsm(path: Path, out: Path, max_px: int = 520) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.enums import Resampling

    if not path.exists():
        return None
    with rasterio.open(path) as ds:
        scale = max(ds.width, ds.height) / float(max_px)
        h = max(1, int(round(ds.height / max(scale, 1.0))))
        w = max(1, int(round(ds.width / max(scale, 1.0))))
        z = ds.read(1, out_shape=(h, w), resampling=Resampling.bilinear)
        nodata = ds.nodata
    z = z.astype(float)
    if nodata is not None:
        z = np.where(z == nodata, np.nan, z)
    _cjk_rcparams()
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    cmap = plt.colormaps["terrain"].copy()
    cmap.set_bad((1, 1, 1, 0))
    im = ax.imshow(z, cmap=cmap)
    ax.set_axis_off()
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.subplots_adjust(0.02, 0.02, 0.88, 0.98)
    return _save_fig(fig, out)


def plot_camera_positions(gps_rows: Sequence[dict], out: Path) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not gps_rows:
        return None
    # 每个曝光只画一次，避免 8 个波段叠在同一点
    seen = {}
    for r in gps_rows:
        key = (round(r["gps0"][0], 2), round(r["gps0"][1], 2))
        seen[key] = r
    rows = list(seen.values())
    x0 = [r["gps0"][0] for r in rows]
    y0 = [r["gps0"][1] for r in rows]
    x1 = [r["gps1"][0] for r in rows]
    y1 = [r["gps1"][1] for r in rows]
    _cjk_rcparams()
    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    # 中文图例不画在图里。Worker 没有中文字体，matplotlib 会把汉字画成方框。
    # 蓝点是初始 POS，绿点是优化 POS，说明由 PDF 用 CID 字体写出。
    ax.scatter(x0, y0, s=18, c="#1f4e9e", marker="D", zorder=2)
    ax.scatter(x1, y1, s=14, c="#2ca02c", marker="D", zorder=3)
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, color="#eeeeee")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#333333")
    return _save_fig(fig, out)


def plot_at_view(
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    points: np.ndarray,
    ground_z: float,
    out: Path,
) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon

    from ms_mosaic.pairs import footprint_polygons

    if not poses:
        return None
    polys = footprint_polygons(cameras, poses, ground_z)
    _cjk_rcparams()
    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    for poly in polys.values():
        xy = np.array(poly.exterior.coords)
        ax.add_patch(MplPolygon(xy, closed=True, facecolor="#d0d0d0", edgecolor="#888888", linewidth=0.3, alpha=0.7))
    pts = np.asarray(points, float)
    if pts.size:
        if len(pts) > 12000:
            rng = np.random.default_rng(0)
            pts = pts[rng.choice(len(pts), 12000, replace=False)]
        ax.scatter(pts[:, 0], pts[:, 1], s=1.5, c="#1f4e9e", marker="o", linewidths=0, zorder=3)
    cx = [p.center[0] for p in poses.values()]
    cy = [p.center[1] for p in poses.values()]
    ax.scatter(cx, cy, s=12, c="#d62728", marker="o", zorder=4)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#333333")
    return _save_fig(fig, out)


_OVERLAP_COLORS = [
    "#e31a1c",  # 1
    "#ff7f00",  # 2
    "#ffd92f",  # 3
    "#a6d854",  # 4
    "#33a02c",  # 5
    "#80cdc1",  # 6
    "#41b6c4",  # 7
    "#1d91c0",  # 8
    "#225ea8",  # 9
    "#6a3d9a",  # >=10
]


def plot_overlap_view(
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    ground_z: float,
    grid: Grid,
    out: Path,
) -> Path | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    from ms_mosaic.pairs import overlap_counts

    if not poses:
        return None
    max_dim = 360
    scale = max(grid.width, grid.height) / float(max_dim)
    gsd = grid.gsd * max(scale, 1.0)
    coarse = Grid.from_bounds(grid.bounds, gsd, grid.crs, snap=False)
    counts = overlap_counts(cameras, poses, ground_z, coarse.transform, coarse.shape)
    cmap = ListedColormap(["#ffffff"] + _OVERLAP_COLORS)
    bounds = list(range(0, 12))
    norm = BoundaryNorm(bounds, cmap.N)
    _cjk_rcparams()
    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    ax.imshow(counts, cmap=cmap, norm=norm, interpolation="nearest")
    cx = [(p.center[0] - coarse.bounds[0]) / coarse.gsd for p in poses.values()]
    cy = [(coarse.bounds[3] - p.center[1]) / coarse.gsd for p in poses.values()]
    ax.scatter(cx, cy, s=8, c="#d62728", marker="o", zorder=4)
    ax.set_axis_off()
    return _save_fig(fig, out)


def render_report_figures(
    *,
    fig_dir: Path,
    files: dict,
    gps_rows: Sequence[dict],
    cameras: dict[int, Camera],
    poses: dict[int, Pose],
    points: np.ndarray,
    ground_z: float,
    grid: Grid,
) -> dict[str, str]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    rgb = files.get("rgb")
    dsm = files.get("dsm")
    try:
        p = thumbnail_ortho(Path(rgb), fig_dir / "ortho.png") if rgb else None
        if p:
            out["ortho"] = str(p)
    except Exception:
        pass
    try:
        p = thumbnail_dsm(Path(dsm), fig_dir / "dsm.png") if dsm else None
        if p:
            out["dsm"] = str(p)
    except Exception:
        pass
    try:
        p = plot_camera_positions(gps_rows, fig_dir / "camera_pos.png")
        if p:
            out["camera_pos"] = str(p)
    except Exception:
        pass
    try:
        p = plot_at_view(cameras, poses, points, ground_z, fig_dir / "at_view.png")
        if p:
            out["at_view"] = str(p)
    except Exception:
        pass
    try:
        p = plot_overlap_view(cameras, poses, ground_z, grid, fig_dir / "overlap.png")
        if p:
            out["overlap"] = str(p)
    except Exception:
        pass
    return out


def cameras_table(
    block: Block,
    at,
    rms_px: float | None,
    *,
    registered: set[int] | None = None,
    rms_by_band: dict[str, float | None] | None = None,
) -> dict:
    """每个波段都进表：空三已解的用 at.cameras，否则按 transfer_band 迁移。"""
    reg = registered if registered is not None else registered_image_ids(at)
    band_rms = rms_by_band or {}
    info = {}
    order = block.band_order()
    for band in order:
        if band not in block.cameras:
            continue
        n_all = len(block.by_band(band))
        if band in at.cameras:
            cam = at.cameras[band]
            n_reg = sum(1 for im in block.by_band(band) if im.index in reg)
        else:
            cams, poses, _ = transfer_band(block, at.cameras, at.poses, band)
            cam = next(iter(cams.values())) if cams else block.cameras[band]
            n_reg = sum(1 for i in poses if i in reg)
        info[band] = {
            "group": order.index(band),
            "model": cam.model,
            "width": cam.width,
            "height": cam.height,
            "f": cam.f,
            "cx": cam.cx,
            "cy": cam.cy,
            "k1": cam.k1,
            "k2": cam.k2,
            "k3": cam.k3,
            "p1": cam.p1,
            "p2": cam.p2,
            "b1": cam.b1,
            "b2": cam.b2,
            "n_images": n_all,
            "n_registered": n_reg,
            "rms_px": band_rms.get(band, rms_px),
        }
    return info
