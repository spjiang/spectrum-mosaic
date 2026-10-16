"""曝光补偿与多频段融合的测试。

重点验证两件事：增益解能把已知的曝光差找回来；多频段融合确实比硬拼和单一宽度
羽化都好 —— 既压掉亮度台阶，又不把纹理糊掉。
"""

import numpy as np
import pytest

from ms_mosaic.blend import (
    OverlapStats,
    accumulate_overlap,
    apply_gains,
    collapse_pyramid,
    laplacian_pyramid,
    merge_overlap,
    multiband_blend,
    seam_step,
    solve_gains,
)
from ms_mosaic.ortho import WarpedStack


def make_stack(pixels, scores, views=None):
    pixels = np.asarray(pixels, np.float32)
    scores = np.asarray(scores, np.float32)
    views = list(range(pixels.shape[0])) if views is None else views
    return WarpedStack((0, 0, pixels.shape[2], pixels.shape[3]), views, pixels, scores)


def test_overlap_stats_normalises_pair_order():
    acc = OverlapStats()
    acc.add(5, 2, 100.0, 200.0, 10)
    assert (2, 5) in acc.counts
    mi, mj = acc.means((2, 5))
    assert mi == pytest.approx(20.0) and mj == pytest.approx(10.0)


def test_overlap_stats_accumulates_across_tiles():
    acc = OverlapStats()
    acc.add(0, 1, 100.0, 50.0, 10)
    acc.add(0, 1, 100.0, 50.0, 10)
    assert acc.counts[(0, 1)] == 20
    assert acc.means((0, 1)) == (pytest.approx(10.0), pytest.approx(5.0))


def test_merge_overlap_sums_two_tiles():
    a, b = OverlapStats(), OverlapStats()
    a.add(0, 1, 100.0, 50.0, 10)
    b.add(0, 1, 200.0, 80.0, 10)
    merge_overlap(a, b)
    assert a.counts[(0, 1)] == 20
    assert a.means((0, 1)) == (pytest.approx(15.0), pytest.approx(6.5))


def test_overlap_stats_ignores_empty():
    acc = OverlapStats()
    acc.add(0, 1, 0.0, 0.0, 0)
    assert acc.images == []


def test_accumulate_overlap_skips_tiny_overlaps():
    px = np.full((2, 1, 10, 10), 100.0, np.float32)
    scores = np.full((2, 10, 10), 0.5, np.float32)
    scores[1, 2:, :] = np.nan  # 仅 20 个格网重叠，低于阈值
    acc = accumulate_overlap(make_stack(px, scores), OverlapStats())
    assert acc.counts == {}


def test_solve_gains_recovers_known_exposure_ratio():
    """两张影像重叠区真值相同、曝光差 1.5 倍，增益比应当把它抵消。"""
    acc = OverlapStats()
    n = 5000
    acc.add(0, 1, 100.0 * n, 150.0 * n, n)
    g = solve_gains(acc)
    assert g[0] / g[1] == pytest.approx(1.5, rel=0.02)


def test_solve_gains_stays_near_one():
    """正则项应当把整体尺度钉在 1 附近，而不是任意漂移。"""
    acc = OverlapStats()
    n = 5000
    acc.add(0, 1, 100.0 * n, 150.0 * n, n)
    g = solve_gains(acc)
    assert 0.8 < np.mean(list(g.values())) < 1.25
    assert np.median(list(g.values())) == pytest.approx(1.0, abs=0.08)


def test_solve_gains_chain_of_three():
    acc = OverlapStats()
    n = 4000
    # 真值同为 100，三张影像曝光分别为 1.0 / 1.2 / 1.44
    acc.add(0, 1, 100.0 * n, 120.0 * n, n)
    acc.add(1, 2, 120.0 * n, 144.0 * n, n)
    g = solve_gains(acc)
    assert g[0] / g[1] == pytest.approx(1.2, rel=0.03)
    assert g[1] / g[2] == pytest.approx(1.2, rel=0.03)


def test_solve_gains_respects_limits():
    acc = OverlapStats()
    n = 5000
    acc.add(0, 1, 10.0 * n, 500.0 * n, n)  # 50 倍曝光差，应被夹住
    g = solve_gains(acc, limits=(0.5, 2.0))
    assert all(0.5 <= v <= 2.0 for v in g.values())


def test_solve_gains_empty():
    assert solve_gains(OverlapStats()) == {}


def test_apply_gains_scales_pixels():
    px = np.full((2, 1, 4, 4), 100.0, np.float32)
    out = apply_gains(make_stack(px, np.ones((2, 4, 4))), {0: 1.5, 1: 0.5})
    assert out.pixels[0].max() == pytest.approx(150.0)
    assert out.pixels[1].max() == pytest.approx(50.0)


def test_apply_gains_defaults_missing_views_to_one():
    px = np.full((2, 1, 4, 4), 100.0, np.float32)
    out = apply_gains(make_stack(px, np.ones((2, 4, 4))), {0: 2.0})
    assert out.pixels[1].max() == pytest.approx(100.0)


def test_apply_gains_per_channel_fixes_color_cast():
    px = np.full((2, 3, 4, 4), 100.0, np.float32)
    px[1, 0] = 150.0  # 第二张偏红
    out = apply_gains(
        make_stack(px, np.ones((2, 4, 4))),
        [{0: 1.0, 1: 100 / 150}, {0: 1.0, 1: 1.0}, {0: 1.0, 1: 1.0}],
    )
    assert out.pixels[1, 0].max() == pytest.approx(100.0, rel=1e-5)
    assert out.pixels[1, 1].max() == pytest.approx(100.0)


def test_laplacian_pyramid_round_trips():
    rng = np.random.default_rng(0)
    img = rng.random((1, 64, 64)).astype(np.float32) * 255
    rebuilt = collapse_pyramid(laplacian_pyramid(img, 5))
    np.testing.assert_allclose(rebuilt, img, atol=1e-3)


def test_laplacian_pyramid_stops_at_small_size():
    img = np.zeros((1, 6, 6), np.float32)
    pyr = laplacian_pyramid(img, 8)
    assert len(pyr) <= 4
    np.testing.assert_allclose(collapse_pyramid(pyr), img, atol=1e-5)


def _two_view_scene(offset=40.0, h=96, w=96, seed=1, shift=0):
    """左右两视角覆盖同一片纹理，右侧整体亮 offset，标号在中线切开。

    shift 给第二个视角一个整像素错位，用来模拟残余配准误差 —— 只有存在错位时，
    「宽羽化」与「多频段融合」对纹理的影响才有区别。
    """
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(seed)
    base = gaussian_filter(rng.random((h, w)).astype(np.float32), 2.0)
    base = (base - base.min()) / (base.max() - base.min()) * 120.0 + 60.0
    second = np.roll(base, shift, axis=1) if shift else base
    px = np.stack([base, second + offset])[:, None].astype(np.float32)
    scores = np.full((2, h, w), 0.5, np.float32)
    labels = np.zeros((h, w), np.int16)
    labels[:, w // 2:] = 1
    return make_stack(px, scores), labels, base


def test_multiband_blend_removes_brightness_step():
    """硬拼会在中线留下 40 灰阶的台阶，多频段融合应当把它压到很小。"""
    stack, labels, base = _two_view_scene(offset=40.0)
    from ms_mosaic.seamline import compose

    hard = compose(stack, labels)
    blended = multiband_blend(stack, labels, levels=5)
    step_hard = seam_step(hard, labels)
    step_blend = seam_step(blended, labels)
    assert step_hard > 30.0, f"硬拼台阶仅 {step_hard:.1f}，这条测试的前提不成立"
    assert step_blend < 0.2 * step_hard, f"融合后台阶 {step_blend:.2f} vs 硬拼 {step_hard:.2f}"


def test_multiband_blend_preserves_texture():
    """融合不能以糊掉纹理为代价：高频能量应与原图相当。"""
    stack, labels, base = _two_view_scene(offset=40.0)
    blended = multiband_blend(stack, labels, levels=5)[0]
    ok = np.isfinite(blended)
    # 用相邻像元差分的标准差衡量高频能量
    hf_in = float(np.std(np.diff(base, axis=1)))
    hf_out = float(np.std(np.diff(np.where(ok, blended, np.nan), axis=1)[np.isfinite(
        np.diff(np.where(ok, blended, np.nan), axis=1))]))
    assert hf_out > 0.7 * hf_in, f"高频能量掉到 {hf_out:.2f}，原图 {hf_in:.2f}"


def test_multiband_blend_beats_wide_feather_on_texture():
    """与「单一宽度羽化」对照：两者都能压台阶，但存在配准残差时羽化会产生重影。

    重影表现为过渡带内高频对比度下降 —— 两幅错位的纹理被平均掉了。多频段融合
    在高频层用很窄的过渡，基本只取一侧的纹理，因此能保住对比度。
    """
    from scipy.ndimage import gaussian_filter

    stack, labels, base = _two_view_scene(offset=40.0, shift=3)
    blended = multiband_blend(stack, labels, levels=5)[0]

    # 单一宽过渡带羽化
    m = (labels == 0).astype(np.float32)
    wide = gaussian_filter(m, 8.0)
    feather = stack.pixels[0, 0] * wide + stack.pixels[1, 0] * (1.0 - wide)

    assert seam_step(feather[None], labels) < 5.0  # 羽化也压住了台阶
    hf = lambda a: float(np.std(np.diff(a, axis=1)))
    center = slice(None), slice(30, 66)
    assert hf(blended[center]) > hf(feather[center]), "多频段融合应当比宽羽化保留更多纹理"


def test_multiband_blend_multiband_channels():
    stack, labels, _ = _two_view_scene(offset=30.0)
    px = np.repeat(stack.pixels, 3, axis=1)
    px[:, 1] *= 0.8
    stack3 = make_stack(px, stack.scores)
    out = multiband_blend(stack3, labels, levels=4)
    assert out.shape == (3, 96, 96)
    assert np.isfinite(out).all()


def test_multiband_blend_marks_uncovered_as_nan():
    stack, labels, _ = _two_view_scene()
    labels = labels.copy()
    labels[:10, :10] = -1
    out = multiband_blend(stack, labels, levels=4)
    assert np.isnan(out[:, :10, :10]).all()
    assert np.isfinite(out[:, 50:, 50:]).all()


def test_multiband_blend_no_views():
    stack = WarpedStack((0, 0, 8, 8), [], np.zeros((0, 1, 8, 8)), np.full((0, 8, 8), np.nan))
    out = multiband_blend(stack, np.full((8, 8), -1, np.int16))
    assert np.isnan(out).all()


def test_seam_step_zero_without_seam():
    labels = np.zeros((10, 10), np.int16)
    assert seam_step(np.ones((1, 10, 10)), labels) == 0.0


def test_gain_and_blend_together_beat_blend_alone():
    """曝光差很大时，先补偿再融合应当优于只融合 —— 两个环节各司其职。"""
    stack, labels, base = _two_view_scene(offset=0.0)
    # 造成倍数关系的曝光差（增益模型正是乘性的）
    px = stack.pixels.copy()
    px[1] *= 1.6
    stack = make_stack(px, stack.scores)

    acc = accumulate_overlap(stack, OverlapStats())
    gains = solve_gains(acc)
    compensated = apply_gains(stack, gains)

    only_blend = multiband_blend(stack, labels, levels=5)[0]
    both = multiband_blend(compensated, labels, levels=5)[0]

    # 与真值（增益归一后的基准）比：补偿后整体色调更接近一致
    def spread(a):
        left = np.nanmean(a[:, :40] / np.nanmean(base[:, :40]))
        right = np.nanmean(a[:, 56:] / np.nanmean(base[:, 56:]))
        return abs(left - right)

    assert spread(both) < spread(only_blend)
