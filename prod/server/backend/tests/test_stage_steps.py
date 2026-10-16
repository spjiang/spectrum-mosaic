from datetime import datetime, timezone

from types import SimpleNamespace

from app.services.stage_steps import parse_stage_steps, persist_stage_steps, stage_run_seconds

LOG = """
2026-10-13 02:05:25,184 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-13 02:05:26 曝光 237 → 可用 237（过滤 {'no_pos': 0}），影像 1896 张（含各波段）
2026-10-13 02:05:28 候选像对 9075 对（按 8 个波段组内选对）
2026-10-13 02:05:28 空三并行 12 进程
2026-10-13 02:07:06 特征提取 1896 张，98.1s
2026-10-13 02:14:16 匹配 8307/9075 对成功，430.5s
2026-10-13 02:14:30 连接点 1467980 条，观测 4264861，平均轨迹长 2.91
2026-10-13 02:21:48 交会  物方点 1467099，观测 4262843
2026-10-13 03:03:43 阶段1 自由内参=f,cx,cy  RMS=5.909px  剔除观测 8090  剩余点 1465972
2026-10-13 03:10:00 密集匹配 10/221 块
2026-10-13 03:10:01,000 INFO job abc start S3_dense 密集匹配
2026-10-13 03:10:02 DSM GSD 由航高/焦距估计为 0.072050 m（正射为其一半）
2026-10-13 03:10:02 高程搜索半宽 8.0 m（由稀疏点起伏估计）
2026-10-13 03:10:10 粗格网密集匹配：20 块 / 8 段 / 4 进程
2026-10-13 03:11:00 粗格网解出 17.9%，细格网搜索 ±8.0 m × 2 轮
2026-10-13 03:11:01 密集匹配：格网 4639x6151 @ 0.0721 m
2026-10-13 03:11:01 密集匹配：221 块 / 8 段 / 4 进程
2026-10-13 03:20:00,000 INFO job abc start S5_ortho 正射波段 ('Color',)
2026-10-13 03:20:01 === 正射 Orthomosaic_pix_surf_group0.tif (Color)  237 张 ===
2026-10-13 03:21:00 正射 Orthomosaic_pix_surf_group0.tif (Color) 10/825 块
2026-10-13 03:48:00 已写出 Orthomosaic_pix_surf_group0.tif（Color 完成）
"""


def test_at_steps_keep_counts_and_durations():
    stages = parse_stage_steps(LOG)
    names = [(s["name"], s["detail"], s["elapsed_s"], s["status"]) for s in stages["S2_at"]]
    assert names[0][0] == "影像筛选"
    assert "1896" in names[0][1]
    feature = next(s for s in stages["S2_at"] if s["name"] == "特征提取")
    assert feature["detail"] == "1896 张 · 12 个进程"
    assert "{" not in names[0][1]
    assert feature["elapsed_s"] == 98.1
    assert feature["status"] == "done"
    matched = next(s for s in stages["S2_at"] if s["name"] == "像对匹配")
    assert "8307/9075" in matched["detail"]
    assert "12 个进程" in matched["detail"]
    assert matched["elapsed_s"] == 430.5
    ba = next(s for s in stages["S2_at"] if s["name"] == "平差阶段1")
    assert "5.909" in ba["detail"]
    assert "1465972" in ba["detail"]
    assert all("10/221" not in s["detail"] for s in stages["S2_at"])


def test_dense_and_ortho_skip_tile_progress():
    stages = parse_stage_steps(LOG)
    dense = stages["S3_dense"]
    assert [s["name"] for s in dense] == ["分辨率", "高程搜索", "粗格网", "细格网"]
    assert "4 个进程" in dense[2]["detail"]
    assert "17.9%" in dense[2]["detail"]
    assert "4 个进程" in dense[3]["detail"]
    ortho = stages["S5_ortho"]
    assert ortho[0]["name"] == "正射波段"
    color = next(s for s in ortho if s["name"] == "正射 Color")
    assert color["elapsed_s"] == 1679.0
    assert all("10/825" not in s["detail"] for s in ortho)


def test_running_stage_appends_the_next_function():
    lines = [ln for ln in LOG.splitlines() if ln.strip()]
    text = "\n".join(lines[:5])
    now = datetime(2026, 10, 13, 2, 20, tzinfo=timezone.utc)
    stages = parse_stage_steps(text, current_stage="S2_at", running=True, now=now)
    assert stages["S2_at"][-1]["name"] == "像对匹配"
    assert stages["S2_at"][-1]["status"] == "running"


def test_ortho_retry_gap_is_not_a_step():
    """第一次正射被杀掉到重新开始的空档，不能记进正射分项。"""
    text = """
2026-10-14 04:42:00,000 INFO job abc start S5_ortho 正射波段 ('Color',)
2026-10-14 10:24:31,000 INFO job abc start S5_ortho 正射波段 ('Color',)
2026-10-14 10:24:40 === 正射 Orthomosaic_pix_surf_group0.tif (Color)  237 张 ===
2026-10-14 10:32:07 已写出 Orthomosaic_pix_surf_group0.tif（Color 完成）
2026-10-14 10:32:08 多光谱正射并发 2 波段 × 2 进程（块进程合计 ≤ 4）
"""
    ortho = parse_stage_steps(text)["S5_ortho"]
    starts = [s for s in ortho if s["name"] == "正射波段"]
    assert len(starts) == 1
    assert starts[0]["elapsed_s"] is None
    color = next(s for s in ortho if s["name"] == "正射 Color")
    assert color["elapsed_s"] == 447.0
    assert all((s["elapsed_s"] or 0) < 3600 for s in ortho)


def test_at_restart_does_not_bill_the_gap_to_screening():
    """空三再次进入时，上一趟交会到这次筛选之间的关机空档不能记成筛选耗时。"""
    text = """
2026-10-14 14:57:19,158 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-14 14:57:21 曝光 237 → 可用 237，影像 1896 张
2026-10-14 14:57:22 候选像对 9075 对（按 8 个波段组内选对）
2026-10-14 15:13:38 交会  物方点 1466529，观测 4262634
2026-10-14 23:56:12,545 INFO job abc start S2_at 进入主计算
2026-10-14 23:56:12,843 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-14 23:56:14 曝光 237 → 可用 237，影像 1896 张
2026-10-14 23:56:15 候选像对 9075 对（按 8 个波段组内选对）
"""
    steps = parse_stage_steps(text)["S2_at"]
    screens = [s for s in steps if s["name"] == "影像筛选"]
    assert len(screens) == 2
    assert screens[1]["elapsed_s"] is None or screens[1]["elapsed_s"] < 120
    assert all((s["elapsed_s"] or 0) < 3600 for s in steps)


def test_persist_stage_steps_writes_job(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "run.log").write_text(LOG, encoding="utf-8")
    job = SimpleNamespace(output_dir=None, log_dir=str(log_dir), current_stage="S2_at", status="succeeded", stage_steps={})
    payload = persist_stage_steps(job)
    feature = next(s for s in job.stage_steps["S2_at"] if s["name"] == "特征提取")
    assert feature["detail"] == "1896 张 · 12 个进程"
    assert feature["elapsed_s"] == 98.1
    assert payload["stages"]["S2_at"][0]["name"] == "影像筛选"
    again = dict(job.stage_steps)
    persist_stage_steps(job)
    assert job.stage_steps == again


def test_screening_uses_its_own_start():
    stages = parse_stage_steps(LOG)
    screen = stages["S2_at"][0]
    assert screen["name"] == "影像筛选"
    assert screen["elapsed_s"] is not None
    assert screen["elapsed_s"] < 5


def test_dead_worker_does_not_extend_the_open_step():
    lines = [ln for ln in LOG.splitlines() if ln.strip()]
    text = "\n".join(lines[:5])
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    stages = parse_stage_steps(text, current_stage="S2_at", running=True, now=now, alive=False)
    assert stages["S2_at"][-1]["status"] == "done"
    assert all((s["elapsed_s"] or 0) < 3600 for s in stages["S2_at"])


def test_new_process_does_not_extend_the_previous_segment():
    """死机后新进程的 start input 不能把空档接到上一段正射上。平差自己跑一个多小时仍要计入。"""
    text = """
2026-10-14 04:42:01,000 INFO job abc start S5_ortho 正射波段 ('Color',)
2026-10-14 04:42:17 曝光统计 Orthomosaic (Color) 140/768 块
2026-10-14 10:24:00,344 INFO job abc start input=/data/in out=/data/out
2026-10-14 10:24:31,000 INFO job abc start S5_ortho 正射波段 ('Color',)
2026-10-14 10:32:07 已写出 Orthomosaic（Color 完成）
2026-10-12 07:40:22,084 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-12 07:56:54 交会  物方点 1，观测 2
2026-10-12 09:38:10 阶段1 自由内参=f,cx,cy  RMS=5.898px  剔除观测 1  剩余点 2
"""
    totals = stage_run_seconds(text)
    assert totals["S5_ortho"] < 20 * 60
    assert totals["S2_at"] > 100 * 60


def test_stage_clock_skips_the_dead_gap():
    text = """
2026-10-14 14:57:19,158 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-14 15:13:38 交会  物方点 1，观测 2
2026-10-14 23:56:12,843 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-14 23:56:15 候选像对 9075 对
"""
    totals = stage_run_seconds(text)
    assert totals["S2_at"] < 20 * 60
    assert totals["S2_at"] > 15 * 60
