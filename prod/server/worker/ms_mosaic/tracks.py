"""连接点构网。

把所有像对的内点匹配用并查集连成轨迹（track），每条轨迹对应一个待交会的物方点。
这就是质量报告里的「连接点」。

一条轨迹若在同一张影像上出现两个及以上特征，说明传递过程中串了错误匹配，
直接整条丢弃 —— 这是 COLMAP / Bundler 的标准做法，比试图修补更稳。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from ms_mosaic.matching import PairMatches

MIN_TRACK_LENGTH = 2
MAX_TRACK_LENGTH = 40


class _UnionFind:
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


@dataclass
class Tracks:
    """observations[t] = [(image_index, feature_index), ...]"""

    observations: list[list[tuple[int, int]]]

    def __len__(self) -> int:
        return len(self.observations)

    @property
    def lengths(self) -> np.ndarray:
        return np.array([len(o) for o in self.observations], int)

    def per_image_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for obs in self.observations:
            for image_index, _ in obs:
                counts[image_index] = counts.get(image_index, 0) + 1
        return counts

    def total_observations(self) -> int:
        return int(sum(len(o) for o in self.observations))


def build_tracks(
    matches: Iterable[PairMatches],
    *,
    min_length: int = MIN_TRACK_LENGTH,
    max_length: int = MAX_TRACK_LENGTH,
) -> Tracks:
    uf = _UnionFind()
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
            continue  # 同一影像出现多个特征，轨迹被污染
        observations.append(sorted(members))
    return Tracks(observations)
