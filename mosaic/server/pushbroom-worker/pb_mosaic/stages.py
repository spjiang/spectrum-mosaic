"""步骤编号、名称和进度权重。前端 pages/pushbroom/labels.ts 与这里一致。"""

from __future__ import annotations

STAGES: tuple[tuple[str, str, float], ...] = (
    ("P0_check", "核对三件套", 2.0),
    ("P1_trajectory", "轨迹与时间", 2.0),
    ("P2_strips", "切航带", 1.0),
    ("P3_align", "航带配准", 5.0),
    ("P4_mosaic", "重采样镶嵌", 82.0),
    ("P5_products", "写成果", 6.0),
    ("P6_report", "报告", 2.0),
)

STAGE_IDS = tuple(s[0] for s in STAGES)
LABELS = {s[0]: s[1] for s in STAGES}


def global_percent(stage_id: str, stage_fraction: float) -> float:
    """某一步完成 stage_fraction（0–1）时的总进度百分比。"""
    done = 0.0
    for sid, _label, weight in STAGES:
        if sid == stage_id:
            return round(done + weight * max(0.0, min(1.0, stage_fraction)), 2)
        done += weight
    raise KeyError(stage_id)
