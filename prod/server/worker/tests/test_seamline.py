"""拼接线图割的测试。

关键验证点是「图割确实在优化那个能量」以及「接缝确实躲开了两视角差异大的地方」，
而不只是「跑得通」。
"""

import numpy as np
import pytest

from ms_mosaic.ortho import WarpedStack
from ms_mosaic.seamline import (
    SeamConfig,
    compose,
    data_cost,
    energy,
    optimal_labels,
    seam_cost_volume,
    seam_edges,
)


def make_stack(pixels, scores):
    """pixels: (n_views, bands, h, w)；scores: (n_views, h, w)，nan 表示无效。"""
    pixels = np.asarray(pixels, np.float32)
    scores = np.asarray(scores, np.float32)
    return WarpedStack((0, 0, pixels.shape[2], pixels.shape[3]),
                       list(range(pixels.shape[0])), pixels, scores)


def test_data_cost_is_low_for_high_score():
    stack = make_stack(np.zeros((2, 1, 4, 4)), np.stack([np.full((4, 4), 0.9),
                                                          np.full((4, 4), 0.1)]))
    d = data_cost(stack, SeamConfig())
    assert d[0].max() < d[1].min()


def test_data_cost_marks_invalid_views():
    scores = np.stack([np.full((4, 4), 0.5), np.full((4, 4), np.nan)])
    d = data_cost(make_stack(np.zeros((2, 1, 4, 4)), scores), SeamConfig())
    assert (d[1] >= 1e6).all()


def test_seam_cost_is_zero_for_identical_views():
    img = np.random.default_rng(0).random((6, 6)).astype(np.float32) * 100
    px = np.stack([img, img])[:, None]
    s = seam_cost_volume(make_stack(px, np.ones((2, 6, 6))), SeamConfig())
    assert s[0, 1].max() == pytest.approx(0.0, abs=1e-5)


def test_seam_cost_grows_with_view_difference():
    rng = np.random.default_rng(1)
    a = rng.random((8, 8)).astype(np.float32) * 100
    px = np.stack([a, a + 30.0])[:, None]
    s = seam_cost_volume(make_stack(px, np.ones((2, 8, 8))), SeamConfig())
    assert s[0, 1].min() > 25.0


def test_single_view_labels_everything_zero():
    scores = np.full((1, 5, 5), 0.5)
    scores[0, 0, 0] = np.nan
    labels = optimal_labels(make_stack(np.zeros((1, 1, 5, 5)), scores))
    assert labels[0, 0] == -1
    assert (labels[1:, 1:] == 0).all()


def test_no_views_returns_all_invalid():
    stack = WarpedStack((0, 0, 4, 4), [], np.zeros((0, 1, 4, 4)), np.full((0, 4, 4), np.nan))
    assert (optimal_labels(stack) == -1).all()


def test_labels_never_choose_invalid_view():
    rng = np.random.default_rng(2)
    h = w = 20
    px = rng.random((2, 1, h, w)).astype(np.float32) * 100
    scores = np.full((2, h, w), 0.5, np.float32)
    scores[1, :, : w // 2] = np.nan  # 视角1 只覆盖右半幅
    labels = optimal_labels(make_stack(px, scores))
    assert (labels[:, : w // 2] == 0).all()


def test_graph_cut_lowers_energy_versus_greedy():
    """图割结果的能量必须不高于逐格取最优质量分的贪心解。"""
    rng = np.random.default_rng(3)
    h = w = 40
    base = rng.random((h, w)).astype(np.float32) * 100
    # 两个视角内容相近但各自带不同噪声，质量分也交错，贪心会切得很碎
    px = np.stack([base + rng.normal(0, 8, (h, w)), base + rng.normal(0, 8, (h, w))]).astype(
        np.float32
    )[:, None]
    scores = rng.random((2, h, w)).astype(np.float32)
    stack = make_stack(px, scores)
    cfg = SeamConfig(smooth_weight=0.2)

    greedy = np.argmin(data_cost(stack, cfg), axis=0).astype(np.int16)
    cut = optimal_labels(stack, cfg)
    assert energy(cut, stack, cfg) <= energy(greedy, stack, cfg) + 1e-6
    # 贪心会产生大量碎片，图割应当显著减少接缝长度
    assert seam_edges(cut).sum() < seam_edges(greedy).sum()


def test_seam_avoids_region_where_views_disagree():
    """左右两视角在中间一条带上差异极大，接缝应当避开那条带。"""
    h, w = 40, 60
    rng = np.random.default_rng(4)
    base = (rng.random((h, w)).astype(np.float32) * 40 + 100)
    a = base.copy()
    b = base.copy()
    # 在 x∈[25,35] 造一条两视角完全不一致的带（模拟移动物体/遮挡残差）
    b[:, 25:35] += 120.0
    px = np.stack([a, b])[:, None].astype(np.float32)
    # 质量分左高右高，逼迫接缝必须落在中部某处
    ramp = np.linspace(1.0, 0.0, w, dtype=np.float32)[None, :].repeat(h, 0)
    scores = np.stack([ramp, 1.0 - ramp])
    labels = optimal_labels(make_stack(px, scores), SeamConfig(smooth_weight=0.5))

    edges = seam_edges(labels)
    cols = np.nonzero(edges.any(axis=0))[0]
    assert cols.size > 0, "应当存在接缝"
    inside = ((cols >= 25) & (cols < 35)).mean()
    assert inside < 0.2, f"{inside:.0%} 的接缝落在了两视角矛盾的带内"


def test_seam_edges_marks_both_sides_of_boundary():
    labels = np.zeros((6, 6), np.int16)
    labels[:, 3:] = 1
    e = seam_edges(labels)
    assert e[:, 2].all() and e[:, 3].all()
    assert not e[:, 0].any()


def test_seam_edges_ignores_invalid_labels():
    labels = np.full((5, 5), -1, np.int16)
    labels[:, :2] = 0
    assert not seam_edges(labels).any()


def test_compose_takes_pixels_from_chosen_view():
    px = np.stack([np.full((4, 4), 10.0), np.full((4, 4), 20.0)])[:, None].astype(np.float32)
    labels = np.array([[0, 0, 1, 1]] * 4, np.int16)
    out = compose(make_stack(px, np.ones((2, 4, 4))), labels)
    assert out[0, 0, 0] == 10.0 and out[0, 0, 3] == 20.0


def test_compose_multiband_and_invalid():
    px = np.stack([np.full((3, 4, 4), 7.0), np.full((3, 4, 4), 9.0)]).astype(np.float32)
    labels = np.full((4, 4), 1, np.int16)
    labels[0, 0] = -1
    out = compose(make_stack(px, np.ones((2, 4, 4))), labels)
    assert out.shape == (3, 4, 4)
    assert np.isnan(out[:, 0, 0]).all()
    assert (out[:, 1, 1] == 9.0).all()


def test_graph_cut_matches_brute_force_optimum():
    """两个标号时 α 扩张等价于一次二元最小割，应当取到全局最优。

    这是对图割构造最硬的检验：小规模下穷举所有标号组合比对能量。
    早先辅助结点接错终端时，这条测试 12/12 全挂。
    """
    import itertools

    rng = np.random.default_rng(0)
    h, w, nv = 3, 4, 2
    cfg = SeamConfig(smooth_weight=0.5)
    for _ in range(8):
        stack = make_stack(
            (rng.random((nv, 1, h, w)) * 100).astype(np.float32),
            rng.random((nv, h, w)).astype(np.float32),
        )
        best = min(
            (energy(np.array(c, np.int16).reshape(h, w), stack, cfg)
             for c in itertools.product(range(nv), repeat=h * w))
        )
        got = energy(optimal_labels(stack, cfg), stack, cfg)
        assert got <= best + 1e-6, f"图割 {got:.4f} 未达到穷举最优 {best:.4f}"


def test_graph_cut_beats_greedy_with_three_views():
    """三个标号时 α 扩张只保证局部最优，但必须明显优于逐格贪心。"""
    import itertools

    rng = np.random.default_rng(5)
    # 3^(h·w) 种组合，格网稍大穷举就爆炸，2x3 已足够暴露构造错误
    h, w, nv = 2, 3, 3
    cfg = SeamConfig(smooth_weight=0.5)
    for _ in range(5):
        stack = make_stack(
            (rng.random((nv, 1, h, w)) * 100).astype(np.float32),
            rng.random((nv, h, w)).astype(np.float32),
        )
        best = min(
            (energy(np.array(c, np.int16).reshape(h, w), stack, cfg)
             for c in itertools.product(range(nv), repeat=h * w))
        )
        greedy = np.argmin(data_cost(stack, cfg), axis=0).astype(np.int16)
        got = energy(optimal_labels(stack, cfg), stack, cfg)
        assert got <= energy(greedy, stack, cfg) + 1e-6
        # α 扩张的理论保证是不劣于 2 倍全局最优
        assert got <= 2.0 * best + 1e-6


def test_expansion_is_stable_when_already_optimal():
    """已经是最优解时再跑图割不应改变结果。"""
    h = w = 24
    px = np.stack([np.zeros((h, w)), np.zeros((h, w))])[:, None].astype(np.float32)
    scores = np.stack([np.full((h, w), 0.9), np.full((h, w), 0.1)]).astype(np.float32)
    stack = make_stack(px, scores)
    first = optimal_labels(stack)
    second = optimal_labels(stack)
    np.testing.assert_array_equal(first, second)
    assert (first == 0).all()
