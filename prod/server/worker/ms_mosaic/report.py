from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_report(out_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    report_dir = out_dir / "report"
    log_dir = out_dir / "log"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("created_at", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
    json_path = report_dir / "quality.json"
    md_path = report_dir / "quality.md"
    log_path = log_dir / "run.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
    json_path.write_text(text, encoding="utf-8")
    log_path.write_text(text, encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    return {
        "report_json": str(json_path.resolve()),
        "report_md": str(md_path.resolve()),
        "log_json": str(log_path.resolve()),
    }


def _json_default(obj):
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    if hasattr(obj, "tolist"):
        return obj.tolist()
    return str(obj)


def _to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 多光谱拼图质量报告",
        "",
        f"- 输入: `{payload.get('input')}`",
        f"- 创建时间: {payload.get('created_at')}",
        f"- 扫描曝光: {payload.get('n_scanned')}",
        f"- 采用曝光: {payload.get('n_shots')}",
        f"- 过滤原因: {payload.get('n_filtered')} 帧（白板 / 地面 / 无 POS）",
        f"- CRS: {payload.get('crs')}",
        f"- 墙钟: {payload.get('elapsed_s')} s",
        "",
        "## 成果",
        "",
        f"- RGB: `{payload.get('files', {}).get('rgb')}`",
    ]
    for band, path in (payload.get("files", {}).get("bands") or {}).items():
        lines.append(f"- {band}: `{path}`")
    rgb_meta = payload.get("rgb_meta") or {}
    if rgb_meta:
        lines += [
            "",
            "## RGB 镶嵌",
            "",
            f"- 景数: {rgb_meta.get('n_scenes')}",
            f"- 尺寸: {rgb_meta.get('shape')}",
            f"- 范围: {rgb_meta.get('bounds')}",
        ]
    lines += [
        "",
        "## 采用帧",
        "",
        ", ".join(str(i) for i in payload.get("shot_indices") or []),
        "",
        "本报告为逻辑跑通版本：POS 直接地理定位 + 镶嵌。尚未做空三 / 密集点云，绝对精度受 GPS（无 RTK）限制。",
        "",
    ]
    return "\n".join(lines) + "\n"
