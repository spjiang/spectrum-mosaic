import numpy as np
import pytest

from ms_mosaic.camera import Camera, Pose
from ms_mosaic.features import extract, root_sift
from ms_mosaic.matching import PairMatches, cross_check
from ms_mosaic.pairs import overlap_ratio, select_pairs
from ms_mosaic.rawio import to_gray8
from ms_mosaic.tracks import build_tracks


def _texture(seed: int = 0, size: int = 512) -> np.ndarray:
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 255, (size // 8, size // 8)).astype(np.float32)
    return np.kron(coarse, np.ones((8, 8), np.float32)).astype(np.uint8)


def test_root_sift_is_l2_normalized():
    rng = np.random.default_rng(0)
    desc = rng.random((50, 128)).astype(np.float32) * 255.0
    out = root_sift(desc)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    assert (out >= 0).all()


def test_root_sift_handles_empty():
    assert root_sift(np.zeros((0, 128), np.float32)).shape == (0, 128)


def test_to_gray8_stretches_uint16():
    arr = np.linspace(1000, 5000, 64 * 64).reshape(64, 64).astype(np.uint16)
    gray = to_gray8(arr)
    assert gray.dtype == np.uint8
    assert gray.min() == 0 and gray.max() == 255


def test_to_gray8_rgb_uses_luma():
    rgb = np.zeros((4, 4, 3), np.uint8)
    rgb[..., 1] = 100  # 纯绿
    assert to_gray8(rgb)[0, 0] == pytest.approx(58, abs=1)


def test_to_gray8_constant_image_does_not_blow_up():
    assert to_gray8(np.full((16, 16), 2000, np.uint16)).max() == 0


def test_extract_spreads_features_across_tiles():
    gray = _texture(1, 512)
    feats = extract(gray, max_features=400, tile_grid=(4, 3))
    assert 100 < len(feats) <= 400
    assert feats.descriptors.shape == (len(feats), 128)
    # 每个 1/4 象限都应该有特征，验证分块起作用
    u, v = feats.keypoints[:, 0], feats.keypoints[:, 1]
    for us, vs in [(u < 256, v < 256), (u >= 256, v < 256), (u < 256, v >= 256), (u >= 256, v >= 256)]:
        assert int((us & vs).sum()) > 0


def test_extract_on_blank_image_returns_empty():
    feats = extract(np.zeros((256, 256), np.uint8), max_features=100)
    assert len(feats) == 0
    assert feats.descriptors.shape == (0, 128)


def test_cross_check_matches_shifted_copy_of_real_features():
    """走真实路径：同一纹理平移 40 px，匹配点的位移应当一致。"""
    base = _texture(7, 640)
    shift = 40
    left = base[:, :-shift]
    right = base[:, shift:]
    fa = extract(left, max_features=600)
    fb = extract(right, max_features=600)
    idx = cross_check(fa.descriptors, fb.descriptors)
    assert idx.shape[0] > 50
    du = fa.keypoints[idx[:, 0], 0] - fb.keypoints[idx[:, 1], 0]
    dv = fa.keypoints[idx[:, 0], 1] - fb.keypoints[idx[:, 1], 1]
    # 绝大多数匹配应落在真实位移 (+40, 0) 上
    good = (np.abs(du - shift) < 2.0) & (np.abs(dv) < 2.0)
    assert good.mean() > 0.9


def test_cross_check_rejects_repetitive_texture():
    """重复纹理（农田垄沟、屋顶瓦片）会产生彼此难分的描述子，ratio test 必须拒绝，
    否则会把错误连接点带进平差。"""
    rng = np.random.default_rng(3)
    base = rng.random(128).astype(np.float32)
    desc = np.tile(base, (60, 1)) + rng.normal(0, 0.002, (60, 128)).astype(np.float32)
    desc /= np.linalg.norm(desc, axis=1, keepdims=True)
    other = np.tile(base, (60, 1)) + rng.normal(0, 0.002, (60, 128)).astype(np.float32)
    other /= np.linalg.norm(other, axis=1, keepdims=True)
    assert cross_check(desc, other).shape[0] == 0


def test_overlap_ratio_uses_smaller_polygon():
    from shapely.geometry import box

    big = box(0, 0, 10, 10)
    small = box(0, 0, 2, 10)
    assert overlap_ratio(big, small) == pytest.approx(1.0)
    assert overlap_ratio(big, box(5, 0, 15, 10)) == pytest.approx(0.5)


def test_select_pairs_picks_neighbours_along_strip():
    cam = Camera.initial("Color", 2048, 1536, kind="rgb")
    cameras, poses = {}, {}
    # 沿东西方向 10 帧，每帧前进 20 m，足迹宽约 107 m → 应互相重叠数帧
    for i in range(10):
        cameras[i] = cam
        poses[i] = Pose.from_ypr(np.array([673000.0 + 20.0 * i, 2620000.0, 1871.5]), 0.0, -90.0, 0.0)
    got = select_pairs(cameras, poses, 1760.0, max_neighbors=12, min_overlap=0.1)
    pairs = {(p.i, p.j) for p in got}
    assert (0, 1) in pairs and (0, 2) in pairs
    assert (0, 9) not in pairs  # 相距 180 m，超出足迹
    assert all(p.i < p.j for p in got)
    assert all(p.overlap >= 0.1 for p in got)


def test_select_pairs_default_threshold_drops_weak_overlap():
    """默认阈值 0.45：实测低于此值的像对匹配成功率为 0，不应进入候选。"""
    from ms_mosaic.pairs import MIN_OVERLAP

    assert MIN_OVERLAP == pytest.approx(0.45)
    cam = Camera.initial("Color", 2048, 1536, kind="rgb")
    cameras, poses = {}, {}
    for i in range(6):
        cameras[i] = cam
        poses[i] = Pose.from_ypr(np.array([673000.0 + 40.0 * i, 2620000.0, 1871.5]), 0.0, -90.0, 0.0)
    got = select_pairs(cameras, poses, 1760.0)
    assert all(p.overlap >= MIN_OVERLAP for p in got)


def test_select_pairs_empty_for_distant_frames():
    cam = Camera.initial("Color", 2048, 1536, kind="rgb")
    cameras = {0: cam, 1: cam}
    poses = {
        0: Pose.from_ypr(np.array([673000.0, 2620000.0, 1871.5]), 0.0, -90.0, 0.0),
        1: Pose.from_ypr(np.array([680000.0, 2620000.0, 1871.5]), 0.0, -90.0, 0.0),
    }
    assert select_pairs(cameras, poses, 1760.0) == []


def test_build_tracks_chains_across_three_images():
    matches = [
        PairMatches(0, 1, np.array([[5, 7]]), "essential"),
        PairMatches(1, 2, np.array([[7, 9]]), "essential"),
    ]
    tracks = build_tracks(matches)
    assert len(tracks) == 1
    assert tracks.observations[0] == [(0, 5), (1, 7), (2, 9)]
    assert tracks.total_observations() == 3
    assert tracks.per_image_counts() == {0: 1, 1: 1, 2: 1}


def test_build_tracks_drops_self_conflicting_track():
    # 0 号图的特征 5 和 6 被传递性地并到同一条轨迹里 → 整条丢弃
    matches = [
        PairMatches(0, 1, np.array([[5, 7]]), "essential"),
        PairMatches(1, 2, np.array([[7, 9]]), "essential"),
        PairMatches(0, 2, np.array([[6, 9]]), "essential"),
    ]
    assert len(build_tracks(matches)) == 0


def test_build_tracks_respects_length_bounds():
    matches = [PairMatches(i, i + 1, np.array([[0, 0]]), "essential") for i in range(6)]
    assert len(build_tracks(matches, min_length=2, max_length=40)) == 1
    assert len(build_tracks(matches, min_length=2, max_length=5)) == 0


def test_build_tracks_empty_input():
    tracks = build_tracks([])
    assert len(tracks) == 0
    assert tracks.lengths.size == 0


def test_extract_file_rebuilds_corrupt_npz(tmp_path):
    from PIL import Image

    from ms_mosaic.features import extract_file

    img = tmp_path / "shot.jpg"
    Image.fromarray(_texture(3, 128)).convert("RGB").save(img, quality=90)
    cache = tmp_path / "cache"
    cache.mkdir()
    bad = cache / "shot.npz"
    bad.write_bytes(b"PK\x03\x04not a real zip file")
    feats = extract_file(img, cache_dir=cache, max_features=80)
    assert feats.descriptors.ndim == 2
    import zipfile

    assert zipfile.is_zipfile(cache / "shot.npz")


def test_load_keypoints_skips_descriptors(tmp_path):
    """缓存里即使有描述子，load_keypoints 也只读关键点数组。"""
    from ms_mosaic.features import load_keypoints

    img = tmp_path / "shot.jpg"
    img.write_bytes(b"placeholder")
    cache = tmp_path / "cache"
    cache.mkdir()
    kps = np.array([[10.0, 20.0, 3.0, 0.1], [30.0, 40.0, 4.0, 0.2]], dtype=np.float32)
    desc = np.zeros((2, 128), dtype=np.float32)
    np.savez_compressed(cache / "shot.npz", keypoints=kps, descriptors=desc)
    got = load_keypoints(img, cache_dir=cache)
    assert np.allclose(got, kps)
    assert got.shape == (2, 4)

