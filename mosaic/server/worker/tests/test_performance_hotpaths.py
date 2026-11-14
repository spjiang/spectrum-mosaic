"""空三热点路径的性能优化回归：结果必须与旧实现一致。"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ms_mosaic.at import MIN_OBS_PER_TRACK, Observation, drop_outliers
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.matching import PairMatches
from ms_mosaic.runner import _band_pair_candidates, _band_pair_candidates_sequential
from ms_mosaic.scene import Block, ImageRef
from ms_mosaic.tracks import Tracks, build_tracks


class _TupleUnionFind:
    """build_tracks 改版前的并查集，测试里当对照。"""

    def __init__(self) -> None:
        self._parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, x: tuple[int, int]) -> tuple[int, int]:
        parent = self._parent.setdefault(x, x)
        while parent != x:
            x, parent = parent, self._parent.setdefault(parent, parent)
            self._parent[x] = parent
        return x

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _sorted_obs(tracks: Tracks) -> list[tuple[tuple[int, int], ...]]:
    return sorted(tuple(o) for o in tracks.observations)


def _reference_build_tracks(
    matches,
    *,
    min_length: int = 2,
    max_length: int = 40,
) -> Tracks:
    uf = _TupleUnionFind()
    nodes: set[tuple[int, int]] = set()
    for pm in matches:
        for fa, fb in pm.indices:
            a = (pm.i, int(fa))
            b = (pm.j, int(fb))
            nodes.add(a)
            nodes.add(b)
            uf.union(a, b)

    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for node in nodes:
        groups.setdefault(uf.find(node), []).append(node)

    observations: list[list[tuple[int, int]]] = []
    for members in groups.values():
        if len(members) < min_length or len(members) > max_length:
            continue
        images = [m[0] for m in members]
        if len(set(images)) != len(images):
            continue
        observations.append(sorted(members))
    return Tracks(observations)


def _synthetic_block() -> Block:
    cam_c = Camera.initial("Color", 1440, 1080, kind="rgb")
    cam_g = Camera.initial("550nm", 1440, 1080, kind="ms")
    cam_n = Camera.initial("850nm", 1440, 1080, kind="ms")
    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.cameras = {"Color": cam_c, "550nm": cam_g, "850nm": cam_n}
    specs = [
        (0, 0, "Color", 0.0),
        (1, 0, "550nm", 0.0),
        (2, 0, "850nm", 0.0),
        (3, 1, "Color", 14.0),
        (4, 1, "550nm", 14.0),
        (5, 1, "850nm", 14.0),
        (6, 2, "Color", 28.0),
        (7, 2, "550nm", 28.0),
        (8, 2, "850nm", 28.0),
    ]
    for idx, shot, band, x in specs:
        block.images.append(
            ImageRef(idx, shot, band, Path(f"{band}_{shot}.tif"), 0 if band == "Color" else 1)
        )
        block.poses[idx] = Pose.from_ypr(np.array([x, 0.0, 80.0]), 0.0, -90.0, 0.0)
        block.gps[idx] = np.array([x, 0.0, 80.0])
    return block


def test_band_pair_candidates_threaded_matches_sequential():
    block = _synthetic_block()
    seq = _band_pair_candidates_sequential(block, ground_z=0.0)
    par = _band_pair_candidates(block, ground_z=0.0)
    assert [(c.i, c.j, round(c.overlap, 9)) for c in seq] == [
        (c.i, c.j, round(c.overlap, 9)) for c in par
    ]


def test_build_tracks_matches_reference_on_chains():
    matches = [
        PairMatches(0, 1, np.array([[5, 7], [6, 8]]), "essential"),
        PairMatches(1, 2, np.array([[7, 9], [8, 10]]), "essential"),
        PairMatches(0, 2, np.array([[6, 9]]), "essential"),
    ]
    ref = _reference_build_tracks(matches)
    got = build_tracks(matches)
    assert got.observations == ref.observations


def test_build_tracks_matches_reference_on_conflicting_track():
    matches = [
        PairMatches(0, 1, np.array([[5, 7]]), "essential"),
        PairMatches(1, 2, np.array([[7, 9]]), "essential"),
        PairMatches(0, 2, np.array([[6, 9]]), "essential"),
    ]
    ref = _reference_build_tracks(matches)
    got = build_tracks(matches)
    assert got.observations == ref.observations


def test_build_tracks_matches_reference_on_many_edges():
    """多像对、每对多条边：像对之间不共享影像，轨迹应全是长度 2。"""
    matches = []
    for k in range(300):
        i, j = k * 2, k * 2 + 1
        n = 80
        idx = np.column_stack([np.arange(n, dtype=np.int32), np.arange(n, dtype=np.int32)])
        matches.append(PairMatches(i, j, idx, "essential"))
    ref = _reference_build_tracks(matches)
    got = build_tracks(matches)
    assert _sorted_obs(got) == _sorted_obs(ref)
    assert len(got) == 24_000
    assert got.total_observations() == ref.total_observations() == 48_000


def _reference_drop_outliers(
    points: np.ndarray,
    observations: list[Observation],
    residual_px: np.ndarray,
    *,
    threshold_px: float = 6.0,
    min_obs: int = MIN_OBS_PER_TRACK,
):
    keep = residual_px <= threshold_px
    survivors = [o for o, k in zip(observations, keep) if k]

    counts: dict[int, int] = {}
    for o in survivors:
        counts[o.point] = counts.get(o.point, 0) + 1
    good_points = {p for p, c in counts.items() if c >= min_obs}

    remap: dict[int, int] = {}
    new_points = []
    for old in sorted(good_points):
        remap[old] = len(new_points)
        new_points.append(points[old])

    new_obs = [Observation(o.image, remap[o.point], o.uv) for o in survivors if o.point in remap]
    return np.array(new_points).reshape(-1, 3), new_obs, int((~keep).sum())


def _assert_drop_outliers_equal(ref, got):
    ref_pts, ref_obs, ref_d = ref
    got_pts, got_obs, got_d = got
    assert ref_d == got_d
    np.testing.assert_allclose(ref_pts, got_pts)
    assert len(ref_obs) == len(got_obs)
    for a, b in zip(ref_obs, got_obs):
        assert a.image == b.image and a.point == b.point
        np.testing.assert_allclose(a.uv, b.uv)


def test_drop_outliers_large_matches_reference():
    rng = np.random.default_rng(7)
    n_pts = 8000
    n_obs = 250_000
    points = rng.normal(size=(n_pts, 3))
    residuals = rng.uniform(0.0, 12.0, n_obs)
    observations = [
        Observation(
            int(rng.integers(0, 250)),
            int(rng.integers(0, n_pts)),
            rng.normal(size=2),
        )
        for _ in range(n_obs)
    ]
    ref = _reference_drop_outliers(points, observations, residuals, threshold_px=6.0)
    got = drop_outliers(points, observations, residuals, threshold_px=6.0)
    _assert_drop_outliers_equal(ref, got)


def test_build_tracks_wall_clock_on_heavy_edges():
    """模拟 1530 内点规模的一部分：4.8 万条边，整段应在数秒内完成。"""
    matches = []
    for k in range(400):
        i, j = k * 2, k * 2 + 1
        n = 120
        idx = np.column_stack([np.arange(n, dtype=np.int32), np.arange(n, dtype=np.int32)])
        matches.append(PairMatches(i, j, idx, "essential"))

    build_tracks(matches)
    t0 = time.perf_counter()
    got = build_tracks(matches)
    elapsed = time.perf_counter() - t0

    assert len(got) == 48_000
    assert elapsed < 8.0
