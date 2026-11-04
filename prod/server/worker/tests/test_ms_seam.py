"""多光谱拼接线：参考波段图割一次，其余波段按曝光号沿用。影像相同则接缝逐格相同。"""

import numpy as np

from ms_mosaic.compose import _seam_reference_index, transfer_seam_labels
from ms_mosaic.ortho import WarpedStack
from ms_mosaic.seamline import optimal_labels


def _stack(views, pixels, scores):
    h, w = pixels.shape[-2:]
    return WarpedStack((0, 0, h, w), list(views), pixels, scores)


def test_transfer_keeps_the_same_shot_and_fills_holes():
    ref_labels = np.array([[0, 1], [-1, 0]], np.int16)
    ref_views = [10, 11]
    ref_shot = {10: 1, 11: 2}
    scores = np.array(
        [
            [[5.0, 1.0], [4.0, 0.2]],
            [[0.1, 3.0], [0.1, 0.1]],
        ],
        np.float32,
    )
    pixels = np.zeros((2, 1, 2, 2), np.float32)
    dst = _stack([20, 21], pixels, scores)
    out = transfer_seam_labels(ref_labels, ref_views, ref_shot, dst, {20: 1, 21: 2})
    # (0,0) 曝光 1 → 视角 0；(0,1) 曝光 2 → 视角 1
    # 参考没有来源的 (1,0) 用本波段最高分，仍是视角 0
    assert out.tolist() == [[0, 1], [0, 0]]


def test_missing_shot_uses_the_best_available_view():
    ref_labels = np.array([[0]], np.int16)
    scores = np.array([[[0.2]], [[4.0]]], np.float32)
    dst = _stack([30, 31], np.zeros((2, 1, 1, 1), np.float32), scores)
    out = transfer_seam_labels(ref_labels, [10], {10: 7}, dst, {30: 8, 31: 9})
    assert out.tolist() == [[1]]


def test_unmapped_shots_refuse_transfer():
    dst = _stack([20], np.zeros((1, 1, 1, 1), np.float32), np.ones((1, 1, 1), np.float32))
    assert transfer_seam_labels(np.zeros((1, 1), np.int16), [10], {}, dst, {20: 1}) is None


def test_reference_band_prefers_550nm_and_skips_edited():
    dummy = _stack([1], np.zeros((1, 1, 1, 1), np.float32), np.ones((1, 1, 1), np.float32))
    prepared = [
        ("450nm", {}, [], dummy),
        ("550nm", {}, ["edited"], dummy),
        ("650nm", {}, [], dummy),
    ]
    assert _seam_reference_index(prepared) == 2
    prepared[1] = ("550nm", {}, [], dummy)
    assert _seam_reference_index(prepared) == 1


def _overlap_stack(views):
    """左视角盖住左侧，右视角盖住右侧，重叠带两边灰度一致，接缝应落在重叠带。"""
    h, w = 48, 64
    pixels = np.full((2, 1, h, w), np.nan, np.float32)
    scores = np.full((2, h, w), np.nan, np.float32)
    pixels[0, 0, :, :40] = 40
    pixels[1, 0, :, 24:] = 180
    pixels[0, 0, :, 24:40] = 90
    pixels[1, 0, :, 24:40] = 90
    scores[0, :, :40] = 1.0
    scores[1, :, 24:] = 0.8
    return _stack(views, pixels, scores)


def test_identical_images_keep_the_same_shot_seam():
    ref = _overlap_stack([10, 11])
    dst = _overlap_stack([20, 21])
    ref_labels = optimal_labels(ref)
    own = optimal_labels(dst)
    moved = transfer_seam_labels(ref_labels, ref.views, {10: 1, 11: 2}, dst, {20: 1, 21: 2})
    assert moved is not None
    ref_shot = np.array([1, 2], np.int16)[ref_labels]
    own_shot = np.array([1, 2], np.int16)[own]
    moved_shot = np.array([1, 2], np.int16)[moved]
    assert np.array_equal(moved_shot, ref_shot)
    assert np.array_equal(moved_shot, own_shot)
    assert (moved >= 0).all()
