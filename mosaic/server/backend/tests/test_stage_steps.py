from datetime import datetime, timezone

from types import SimpleNamespace

from app.services.stage_steps import parse_stage_steps, persist_stage_steps, stage_run_seconds

LOG = """
2026-10-13 02:05:25,184 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-13 02:05:26 曝光 237 → 可用 237（过滤 {'no_pos': 0}），影像 1896 张（含各波段）
2026-10-13 02:05:28 候选像对 9075 对（按 8 个波段组内选对）
2026-10-13 02:05:28 空三并行 12 进程
2026-10-13 02:07:06 特征提取 1896 张，98.1s
2026-10-13 02:08:00 已提取 400/1896 张，用时 30 秒
2026-10-13 02:10:00 已匹配 2000/9075 对，用时 120 秒
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
    assert sum(1 for s in stages["S2_at"] if s["name"] == "特征提取") == 1
    matched = next(s for s in stages["S2_at"] if s["name"] == "像对匹配")
    assert "8307/9075" in matched["detail"]
    assert "12 个进程" in matched["detail"]
    assert matched["elapsed_s"] == 430.5
    assert sum(1 for s in stages["S2_at"] if s["name"] == "像对匹配") == 1
    ba = next(s for s in stages["S2_at"] if s["name"] == "平差阶段1")
    assert "5.909" in ba["detail"]
    assert "1465972" in ba["detail"]
    assert all("10/221" not in s["detail"] for s in stages["S2_at"])


def test_band_list_does_not_become_the_ba_summary():
    """空三波段打在候选像对之前，不能被当成平差，否则合计行会插到筛选后面。"""
    text = """
2026-11-10 01:00:00,000 INFO job abc start S2_at 空三 / 稀疏重建
2026-11-10 01:00:02 曝光 1992 → 可用 1743（过滤 {}），影像 1992 张（含各波段）
2026-11-10 01:00:03 空三波段 7 个：Color/450nm
2026-11-10 01:00:03 空三只解 Color，1743 张。未列入的波段不做特征
2026-11-10 01:00:04 候选像对 9565 对（按 7 个波段组内选对；若全影像两两组合为 1）
2026-11-10 01:00:05 空三并行 12 进程
2026-11-10 01:00:06 特征提取：GPU 0 Tesla T4，显存上限 12.3 GB
2026-11-10 01:00:58 特征提取 1743 张，51.0s
2026-11-10 01:14:22 匹配 9405/9565 对成功，804.0s
2026-11-10 01:14:30 连接点 10 条，观测 20，平均轨迹长 2.00
2026-11-10 01:20:00 交会  物方点 10，观测 20
2026-11-10 01:30:00 阶段1 自由内参=f RMS=1px 剔除观测 1 剩余点 9
2026-11-10 01:35:00 空三 900.0s，稀疏重建合计 1800.0s
"""
    names = [s["name"] for s in parse_stage_steps(text)["S2_at"]]
    assert names.index("影像筛选") < names.index("候选像对")
    assert names.index("候选像对") < names.index("像对匹配")
    assert names.index("像对匹配") < names.index("前方交会")
    assert names.index("前方交会") < names.index("平差")
    assert names.count("平差") == 1


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


def test_gpu_steps_are_marked_and_cpu_fallback_is_not():
    text = """
2026-11-05 01:00:00,000 INFO job abc start S2_at 空三 / 稀疏重建
2026-11-05 01:00:01 特征提取：GPU 0 NVIDIA，显存上限 20.0 GB
2026-11-05 01:00:20 特征提取 10 张，12.0s
2026-11-05 01:00:21 匹配：CPU FLANN，8 进程
2026-11-05 01:01:00 前方交会：GPU 0 NVIDIA，显存上限 20.0 GB
2026-11-05 01:02:00 交会  物方点 10，观测 20
2026-11-05 01:02:01 平差：GPU 1 NVIDIA，显存上限 10.0 GB
2026-11-05 01:03:00 阶段1 自由内参=f RMS=0.4px 剔除观测 1 剩余点 9
2026-11-05 01:03:01 平差：GPU 0 NVIDIA，显存上限 10.0 GB
2026-11-05 01:04:00 阶段2 自由内参=f RMS=0.3px 剔除观测 0 剩余点 9
2026-11-05 01:04:01 平差：没有卡，使用 CPU
2026-11-05 01:04:30 阶段3 自由内参=f RMS=0.2px 剔除观测 0 剩余点 9
2026-11-05 01:04:01,000 INFO job abc start S3_dense 密集匹配
2026-11-05 01:04:02 粗格网密集匹配：半全局聚合 GPU 0、1。窗口相关仍在 CPU
2026-11-05 01:05:00 粗格网解出 15.5%，细格网搜索 ±8.0 m × 2 轮
2026-11-05 01:05:01 密集匹配：格网 10x10 @ 0.1 m
2026-11-05 01:05:01 密集匹配：使用 CPU
2026-11-05 01:06:00,000 INFO job abc start S5_ortho 正射波段 ('Color', '450nm')
2026-11-05 01:06:01 正射 group0.tif (Color)：GPU 0 NVIDIA，显存上限 8.0 GB
2026-11-05 01:07:00 已写出 a.tif（Color 完成）
2026-11-05 01:07:01 正射 group1.tif (450nm)：32 进程超过 GPU 上下文上限 8，重采样用 CPU
2026-11-05 01:08:00 已写出 b.tif（450nm 完成）
"""
    stages = parse_stage_steps(text)
    at = {s["name"]: s for s in stages["S2_at"]}
    assert at["特征提取"]["gpu"] is True
    assert "gpu" not in at["像对匹配"]
    assert at["前方交会"]["gpu"] is True
    assert at["平差阶段1"]["gpu"] is True
    assert at["平差阶段2"]["gpu"] is True
    assert "gpu" not in at["平差阶段3"]
    dense = {s["name"]: s for s in stages["S3_dense"]}
    assert dense["粗格网"]["gpu"] is True
    assert "gpu" not in dense["细格网"]
    ortho = {s["name"]: s for s in stages["S5_ortho"]}
    assert ortho["正射 Color"]["gpu"] is True
    assert "gpu" not in ortho["正射 450nm"]


def test_fine_grid_time_runs_until_the_stage_ends():
    """细格网那一行在开扫前打出。真正扫完的时间要算到这一步，不能停在下一行。"""
    text = """
2026-10-31 01:00:00,000 INFO job abc start S3_dense 密集匹配
2026-10-31 01:00:01 高程搜索半宽 8.0 m（由稀疏点起伏估计）
2026-10-31 01:03:09 粗格网解出 15.5%，细格网搜索 ±8.0 m × 2 轮
2026-10-31 01:03:10 密集匹配：格网 6280x6960 @ 0.0932 m，±8.0 m × 48 层
2026-10-31 01:03:10 密集匹配：半全局聚合 GPU 0、1。窗口相关仍在 CPU
2026-10-31 01:03:11 密集匹配：20 块 / 8 段 / 8 进程
2026-10-31 01:14:10 密集匹配 100/323 块
2026-10-31 01:14:11,000 INFO job abc start S4_dsm 写出 DSM
"""
    dense = parse_stage_steps(text)["S3_dense"]
    assert [s["name"] for s in dense].count("细格网") == 1
    fine = next(s for s in dense if s["name"] == "细格网")
    assert fine["elapsed_s"] == 660.0
    assert "6280x6960" in fine["detail"]
    assert "GPU" in fine["detail"]
    assert "8 个进程" in fine["detail"]
    coarse = next(s for s in dense if s["name"] == "粗格网")
    assert coarse["elapsed_s"] == 188.0


def test_running_stage_appends_the_next_function():
    lines = [ln for ln in LOG.splitlines() if ln.strip()]
    text = "\n".join(lines[:5])
    now = datetime(2026, 10, 13, 2, 20, tzinfo=timezone.utc)
    stages = parse_stage_steps(text, current_stage="S2_at", running=True, now=now)
    assert stages["S2_at"][-1]["name"] == "像对匹配"
    assert stages["S2_at"][-1]["status"] == "running"


def test_shared_ms_ortho_is_not_billed_to_the_first_band():
    """几何共用的七个波段一起算。这段时间不能整笔记到先写出的 450nm 上。"""
    text = """
2026-10-25 21:04:36,363 INFO job abc start S5_ortho 正射波段 ('Color', '450nm', '550nm', '650nm')
2026-10-25 21:04:47 === 正射 Orthomosaic_pix_surf_group0.tif (Color)  249 张 ===
2026-10-25 21:11:07 已写出 Orthomosaic_pix_surf_group0.tif（Color 完成）
2026-10-25 21:11:08 多光谱正射几何共用 450nm/550nm/650nm，48 进程。视角上限、立面层数、图割轮数与分波段相同
2026-10-25 21:12:00 正射多光谱（几何共用 450nm/550nm/650nm） 10/1221 块
2026-10-25 22:05:00 正射多光谱（几何共用 450nm/550nm/650nm） 1221/1221 块
2026-10-25 22:06:16 已写出 group1.tif（450nm 完成）
2026-10-25 22:07:34 已写出 group2.tif（550nm 完成）
2026-10-25 22:08:51 已写出 group3.tif（650nm 完成）
"""
    by = {s["name"]: s for s in parse_stage_steps(text)["S5_ortho"]}
    assert by["正射 Color"]["elapsed_s"] == 380.0
    assert by["多光谱正射"]["elapsed_s"] == 3232.0
    assert by["多光谱正射"]["detail"] == "450nm/550nm/650nm · 48 个进程"
    assert by["正射 450nm"]["elapsed_s"] == 76.0
    assert by["正射 550nm"]["elapsed_s"] == 78.0
    assert by["正射 650nm"]["elapsed_s"] == 77.0


def test_shared_ms_ortho_uses_log_span_not_printed_seconds():
    """共同计算按几何共用到计算完成的日志起止。自报秒数偏短时不以它入账，写出仍从完成行往下计。"""
    text = """
2026-10-25 21:04:36,363 INFO job abc start S5_ortho 正射波段 ('Color', '450nm')
2026-10-25 21:04:47 === 正射 Orthomosaic_pix_surf_group0.tif (Color)  249 张 ===
2026-10-25 21:11:07 已写出 Orthomosaic_pix_surf_group0.tif（Color 完成）
2026-10-25 21:11:08 多光谱正射几何共用 450nm/550nm，32 进程。视角上限与分波段相同
2026-10-25 22:05:00 正射多光谱（几何共用 450nm/550nm） 1221/1221 块
2026-10-25 22:05:01 多光谱正射计算完成，3233.4s
2026-10-25 22:06:16 已写出 group1.tif（450nm 完成）
2026-10-25 22:07:32 已写出 group2.tif（550nm 完成）
"""
    by = {s["name"]: s for s in parse_stage_steps(text)["S5_ortho"]}
    assert by["多光谱正射"]["elapsed_s"] == 3233.0
    assert "450nm/550nm" in by["多光谱正射"]["detail"]
    assert by["正射 450nm"]["elapsed_s"] == 75.0
    assert by["正射 550nm"]["elapsed_s"] == 76.0
    assert sum(1 for s in parse_stage_steps(text)["S5_ortho"] if s["name"] == "多光谱正射") == 1


def test_shared_ms_ortho_ignores_short_printed_seconds():
    """虚拟机单调钟偏慢时，日志里的自报秒数会远小于起止间隔，仍按起止入账。"""
    text = """
2026-11-06 11:58:38,000 INFO job abc start S5_ortho 正射波段 ('Color', '550nm')
2026-11-06 11:58:40 === 正射 color.tif (Color)  668 张 ===
2026-11-06 12:04:56 已写出 color.tif（Color 完成）
2026-11-06 12:04:57 多光谱正射几何共用 450nm/550nm，10 进程。拼接线在参考波段上图割一次
2026-11-06 12:05:00 正射多光谱（几何共用 450nm/550nm） 10/1224 块
2026-11-06 17:27:03 正射多光谱（几何共用 450nm/550nm） 1224/1224 块
2026-11-06 17:27:04 多光谱正射计算完成，1867.4s
2026-11-06 17:43:14 已写出 b550.tif（550nm 完成）
2026-11-06 17:43:15 已写出 b450.tif（450nm 完成）
"""
    by = {s["name"]: s for s in parse_stage_steps(text)["S5_ortho"]}
    assert by["多光谱正射"]["elapsed_s"] == 19327.0
    assert by["正射 550nm"]["elapsed_s"] == 970.0
    assert by["正射 450nm"]["elapsed_s"] == 1.0


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


def test_closed_stage3_is_one_step_not_a_third_adjustment():
    text = """
2026-10-14 23:02:00,000 INFO job abc start S2_at 空三 / 稀疏重建
2026-10-14 23:02:08 阶段2 自由内参=f,cx,cy,k1,k2  RMS=0.585px  剔除观测 812  剩余点 1829855
2026-10-14 23:02:09 平差阶段3已关闭，停在阶段2（焦距、主点、k1、k2）
"""
    steps = parse_stage_steps(text)["S2_at"]
    names = [s["name"] for s in steps]
    assert names == ["平差阶段2", "平差阶段3"]
    assert steps[1]["detail"].startswith("已关闭")



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


def test_stage_clock_skips_sleep_gap_within_one_stage():
    """Maintenance Sleep 等：同一阶段两条日志之间超过 120s 的空档不计入。"""
    text = """
2026-11-04 10:00:00 INFO job abc start S5_ortho 正射波段 ('Color',)
2026-11-04 10:20:00 正射 Orthomosaic (Color) 10/825 块
2026-11-04 15:00:00 正射 Orthomosaic (Color) 20/825 块
2026-11-04 15:10:00 已写出 Orthomosaic_pix_surf_group0.tif（Color 完成）
"""
    totals = stage_run_seconds(text)
    assert totals["S5_ortho"] < 35 * 60
    assert totals["S5_ortho"] > 25 * 60
