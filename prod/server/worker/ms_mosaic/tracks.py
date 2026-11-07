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
# 8192 特征点上限；影像号左移 13 位再与特征下标或起来，并查集用整型键比元组省哈希。
_FEATURE_BITS = 13
_FEATURE_MASK = (1 << _FEATURE_BITS) - 1


def _node_key(image: int, feature: int) -> int:
    return (int(image) << _FEATURE_BITS) | (int(feature) & _FEATURE_MASK)


def _split_node(key: int) -> tuple[int, int]:
    return int(key >> _FEATURE_BITS), int(key & _FEATURE_MASK)


class _UnionFind:
    __slots__ = ("_parent", "_rank")

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}
        self._rank: dict[int, int] = {}

    def find(self, x: int) -> int:
        parent = self._parent
        if x not in parent:
            parent[x] = x
            self._rank[x] = 0
            return x
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        rank = self._rank
        parent = self._parent
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1


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

    def release(self) -> None:
        """交会之后平差只读观测列表，丢掉轨迹把内存还给主进程。"""
        self.observations = []


def build_tracks(
    matches: Iterable[PairMatches],
    *,
    min_length: int = MIN_TRACK_LENGTH,
    max_length: int = MAX_TRACK_LENGTH,
) -> Tracks:
    uf = _UnionFind()
    nodes: set[int] = set()
    for pm in matches:
        if len(pm) == 0:
            continue
        idx = pm.indices
        keys_a = (int(pm.i) << _FEATURE_BITS) | idx[:, 0].astype(np.int64, copy=False)
        keys_b = (int(pm.j) << _FEATURE_BITS) | idx[:, 1].astype(np.int64, copy=False)
        for ka, kb in zip(keys_a, keys_b, strict=False):
            ka_i, kb_i = int(ka), int(kb)
            nodes.add(ka_i)
            nodes.add(kb_i)
            uf.union(ka_i, kb_i)

    groups: dict[int, list[tuple[int, int]]] = {}
    for node in nodes:
        groups.setdefault(uf.find(node), []).append(_split_node(node))

    observations: list[list[tuple[int, int]]] = []
    for members in groups.values():
        if len(members) < min_length or len(members) > max_length:
            continue
        images = [m[0] for m in members]
        if len(set(images)) != len(images):
            continue  # 同一影像出现多个特征，轨迹被污染
        observations.append(sorted(members))
    return Tracks(observations)
