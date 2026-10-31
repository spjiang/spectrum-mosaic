"""run_sparse 收尾回归：空三之后组装结果、落检查点、续跑统计不丢。

任务 #18 连续三次在 2 小时空三之后崩在 return（引用了已 del 的 matches / feats），
检查点没落盘，重试又从头跑。这里把重计算换成桩，专测空三之后的路径。
"""

from pathlib import Path

import numpy as np

from ms_mosaic import runner
from ms_mosaic.at import ATResult, Observation
from ms_mosaic.camera import Camera, Pose
from ms_mosaic.checkpoint import apply_payload, load_at_payload, save_sparse
from ms_mosaic.matching import PairMatches
from ms_mosaic.qa import band_rms_px, observation_summary, registered_image_ids
from ms_mosaic.scene import Block, ImageRef


def _block() -> Block:
    block = Block(crs="EPSG:32650", ground_z=0.0)
    block.cameras = {
        "Color": Camera.initial("Color", 1440, 1080, kind="rgb"),
        "550nm": Camera.initial("550nm", 1440, 1080, kind="ms"),
    }
    for idx, shot, band, x in [
        (0, 0, "Color", 0.0),
        (1, 0, "550nm", 0.0),
        (2, 1, "Color", 12.0),
        (3, 1, "550nm", 12.0),
    ]:
        block.images.append(ImageRef(idx, shot, band, Path(f"/in/{band}_{shot}.tif"), 0 if band == "Color" else 1))
        block.poses[idx] = Pose.from_ypr(np.array([x, 0.0, 80.0]), 0.0, -90.0, 0.0)
        block.gps[idx] = np.array([x, 0.0, 80.0])
    return block


def _fake_at(cameras, poses, tracks, features, *_a, **_k):
    assert len(features) == 4
    features.clear()  # 与真 run_at 一致：交会后清空关键点
    obs = [
        Observation(0, 0, np.zeros(2)),
        Observation(2, 0, np.zeros(2)),
        Observation(0, 1, np.zeros(2)),
        Observation(2, 1, np.zeros(2)),
        Observation(1, 2, np.zeros(2)),
        Observation(3, 2, np.zeros(2)),
    ]
    return ATResult(
        cameras=dict(cameras),
        poses=dict(poses),
        points=np.zeros((3, 3)),
        observations=obs,
        residuals_px=np.array([0.3, 0.4, 0.5, 0.6, 1.0, 2.0]),
        stats={"rms_reprojection_px": 0.9, "registered_ids": [0, 1, 2, 3]},
    )


def _run(monkeypatch, tmp_path: Path):
    block = _block()
    monkeypatch.setattr(runner, "scan_directory", lambda *_a, **_k: [object()] * 2)
    monkeypatch.setattr(runner, "usable_shots", lambda shots, **_k: ({0: None, 1: None}, {}))
    monkeypatch.setattr(runner, "build_block", lambda _keep: block)
    monkeypatch.setattr(runner, "_extract_all", lambda jobs, workers, log=None: None)
    matches = [
        PairMatches(0, 2, np.array([[0, 0], [1, 1]]), "essential"),
        PairMatches(1, 3, np.array([[0, 0]]), "essential"),
    ]
    monkeypatch.setattr(runner, "_match_all", lambda *_a, **_k: list(matches))
    kp = {"Color_0": 7, "Color_1": 9, "550nm_0": 3, "550nm_1": 5}
    monkeypatch.setattr(
        runner, "load_keypoints", lambda path, cache_dir: np.zeros((kp[Path(path).stem], 2))
    )
    monkeypatch.setattr(runner, "run_at", _fake_at)
    return runner.run_sparse(tmp_path / "in", tmp_path / "cache" / "features", workers=1)


def test_run_sparse_returns_after_at(monkeypatch, tmp_path: Path):
    sparse = _run(monkeypatch, tmp_path)
    assert sparse.counts["n_matched_pairs"] == 2
    assert sparse.counts["n_keypoints"] == 7 + 9 + 3 + 5
    assert sparse.keypoint_counts == {0: 7, 2: 9, 1: 3, 3: 5}
    assert sorted((m.i, m.j, len(m)) for m in sparse.matches) == [(0, 2, 2), (1, 3, 1)]
    assert len(sparse.tracks) == 3
    assert sparse.tracks.per_image_counts() == {0: 2, 2: 2, 1: 1, 3: 1}
    # 逐条观测已释放，摘要保留
    assert sparse.at.observations == []
    assert sparse.at.obs_counts == {0: 2, 1: 1, 2: 2, 3: 1}
    assert registered_image_ids(sparse.at, sparse.tracks) == {0, 1, 2, 3}


def test_checkpoint_keeps_report_stats(monkeypatch, tmp_path: Path):
    sparse = _run(monkeypatch, tmp_path)
    path = tmp_path / "at_result.npz"
    save_sparse(path, sparse)
    loaded = apply_payload(_block(), load_at_payload(path))
    assert sorted((m.i, m.j, len(m)) for m in loaded.matches) == [(0, 2, 2), (1, 3, 1)]
    assert loaded.keypoint_counts == sparse.keypoint_counts
    assert len(loaded.tracks) == 3
    assert loaded.tracks.per_image_counts() == sparse.tracks.per_image_counts()
    assert observation_summary(loaded.at) == observation_summary(sparse.at)
    rms = band_rms_px(loaded.block, loaded.at)
    np.testing.assert_allclose(rms["Color"], np.sqrt(np.mean(np.square([0.3, 0.4, 0.5, 0.6]))))
    np.testing.assert_allclose(rms["550nm"], np.sqrt(np.mean(np.square([1.0, 2.0]))))


def test_map_progress_keeps_submit_order():
    """后完成的任务不能插到结果前面，进度行要写出已完成数量。"""
    import time
    from concurrent.futures import ThreadPoolExecutor

    logs = []

    def work(n):
        time.sleep(0.02 if n % 2 else 0.0)
        return n * 3

    with ThreadPoolExecutor(max_workers=4) as pool:
        got = runner._map_progress(
            pool, work, list(range(12)), log=logs.append, label="提取", unit="张", chunksize=3, every_s=0
        )
    assert got == [n * 3 for n in range(12)]
    assert logs[-1].startswith("已提取 12/12 张")
    assert all(line.startswith("已提取 ") for line in logs)
