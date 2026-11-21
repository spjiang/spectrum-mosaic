"""report.json 与 report.md：架次、时间修正、航带、配准、成果、处理假设。失败时也写，带原因。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pb_mosaic.stages import LABELS

ASSUMPTIONS = (
    "直接地理定位：每行位置取自 GPS 定位按时间插值，加上传感器安装参数（像元方向、沿航向偏移、狭缝中心）。",
    "按平地、垂直下视投影，未使用地形；地面起伏会带来旁向位置误差，约为 起伏高差 × 旁向距离 / 航高。",
    "未使用 gps 文件中的姿态列：用 0414 架次检验，加入后航带间错位反而变大，判断相机在增稳云台上。",
    "单点 GPS 的绝对位置误差为米级；航带间配准只消除航带之间的相对错位，不提高整体绝对精度。",
    "航带配准分两步：先在接缝两侧各 4 米内量整条平移；再把全部重叠区切块，逐条估计平移细化量和狭缝剪切"
    "（狭缝与航迹不垂直，离中线越远沿航向错开越多，往返方向相反）。全体航带共同的剪切从重叠区量不出来，"
    "按往返两个方向平均剪切互为相反数处理。",
    "传感器标定自检由往返航带的整体错开反推，受各航带 GPS 误差影响，航带越多越可靠；单架次结果仅供复核。",
    "重采样为最近邻，每个成果像元的全部波段来自同一个原始像元，DN 不做插值、匀色或混合。",
    "重叠区取离狭缝中心最近的航带，接缝落在相邻航带中间。",
    "DN 为 0 表示无数据（data ignore value = 0），原始 DN 恰为 0 的像元与无数据无法区分。",
)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_report(report_dir: Path, report: dict[str, Any]) -> None:
    report = dict(report)
    report["assumptions"] = list(ASSUMPTIONS)
    _write_text(report_dir / "report.json", json.dumps(report, ensure_ascii=False, indent=2))
    _write_text(report_dir / "report.md", render_markdown(report))


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return str(n)


def render_markdown(r: dict[str, Any]) -> str:
    out: list[str] = []
    flight = r.get("flight") or {}
    ok = r.get("status") == "succeeded"
    out.append(f"# 推扫拼图报告：{flight.get('name') or r.get('input_dir', '')}")
    out.append("")
    out.append(f"- 结果：{'成功' if ok else '失败'}")
    if r.get("error"):
        out.append(f"- 原因：{r['error']}")
    out.append(f"- 处理程序：pb_mosaic {r.get('worker_version', '')}")
    out.append(f"- 生成时间：{r.get('generated_at', '')}")
    out.append(f"- 输入：`{r.get('input_dir', '')}`")
    out.append(f"- 输出：`{r.get('output_dir', '')}`")
    out.append("")

    if flight:
        out.append("## 架次")
        out.append("")
        out.append(f"- 立方体：{flight.get('samples')} 列 × {flight.get('lines')} 行 × {flight.get('bands')} 波段")
        if flight.get("wavelength_min") is not None:
            out.append(f"- 波长：{flight['wavelength_min']:.1f}–{flight['wavelength_max']:.1f} nm")
        out.append(f"- 定位：{flight.get('gps_records')} 条，时长 {flight.get('duration_s')} 秒")
        out.append("")
    warnings = r.get("warnings") or []
    if warnings:
        out.append("## 提示")
        out.append("")
        out.extend(f"- {w}" for w in warnings)
        out.append("")

    clock = r.get("line_clock")
    if clock:
        out.append("## 行号与时间")
        out.append("")
        out.append(f"- {clock.get('note', '')}")
        out.append("")

    sensor = r.get("sensor")
    if sensor:
        out.append("## 传感器几何")
        out.append("")
        out.append(f"- 旁向分辨率：{sensor['gsd_cross_m'] * 100:.2f} cm（航高 {sensor.get('flight_height_m')} m）")
        out.append(f"- 像元方向：{'向航迹右侧递增' if sensor['sign'] == 1 else '向航迹左侧递增'}")
        out.append(f"- 狭缝中心：第 {sensor['center_px']:.1f} 像元；沿航向偏移 {sensor['along_offset_m']:.2f} m")
        out.append(f"- 参数来源：{r.get('sensor_source', '')}")
        out.append("")

    strips = r.get("strips") or []
    if strips:
        out.append("## 航带")
        out.append("")
        out.append("| 航带 | 行范围 | 航向 | 长度 | 地速 | 配准修正（东, 北） | 狭缝剪切 | 成果像元 |")
        out.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for s in strips:
            off = s.get("align_offset_east_north_m") or [0, 0]
            out.append(
                f"| {s['index']} | {s['line_start']}–{s['line_end']} | {s['heading_deg']:.1f}° | "
                f"{s['length_m']:.1f} m | {s['speed_mps']:.2f} m/s | {off[0]:+.2f}, {off[1]:+.2f} m | "
                f"{s.get('shear_deg', 0.0):+.2f}° | {s.get('mosaic_pixels', '—')} |"
            )
        out.append("")

    align = r.get("alignment")
    if align:
        out.append("## 航带间配准")
        out.append("")
        out.append(f"- {align.get('note', '')}")
        pairs = align.get("pairs") or []
        if pairs:
            out.append("")
            out.append("| 航带对 | 接缝 | 重叠 | 相关性 | 平移（东, 北） | 残差 | 采用 | 说明 |")
            out.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for p in pairs:
                sh = p["shift_east_north_m"]
                res = "—" if p.get("residual_m") is None else f"{p['residual_m']:.2f} m"
                seam = f"{p.get('seam_m2', 0):.0f} m²" if p.get("seam") else "—"
                out.append(
                    f"| {p['strips'][0]}–{p['strips'][1]} | {seam} | {p['overlap_m2']:.0f} m² | {p['ncc']:.2f} | "
                    f"{sh[0]:+.2f}, {sh[1]:+.2f} m | {res} | {'是' if p['used'] else '否'} | {p.get('reason') or ''} |"
                )
        out.append("")
        refine = align.get("refine") or {}
        if refine.get("note"):
            out.append("### 精配准")
            out.append("")
            out.append(f"- {refine['note']}")
            rp = refine.get("pairs") or []
            if rp:
                out.append("")
                out.append("| 航带对 | 接缝 | 修正前块数 | 修正前错位中位 | 修正后块数 | 修正后错位中位 |")
                out.append("| --- | --- | --- | --- | --- | --- |")
                fmt = lambda v: "—" if v is None else f"{v:.2f} m"  # noqa: E731
                for p in rp:
                    out.append(
                        f"| {p['strips'][0]}–{p['strips'][1]} | {'是' if p['seam'] else '—'} | {p['tiles_before']} | "
                        f"{fmt(p['median_before_m'])} | {p['tiles_after']} | {fmt(p['median_after_m'])} |"
                    )
            out.append("")
        tie = align.get("tiepoints") or {}
        if tie.get("note"):
            out.append("### 特征点")
            out.append("")
            out.append(f"- {tie['note']}")
            out.append("")

    cal = r.get("calibration")
    if cal:
        out.append("## 传感器标定自检")
        out.append("")
        out.append(f"- {cal.get('note', '')}")
        if cal.get("shear_backward_deg") is not None:
            diff = cal["shear_forward_deg"] - cal["shear_backward_deg"]
            out.append(f"- 往返狭缝剪切相差 {diff:+.2f}°（偏流随航向翻转）")
        if cal.get("warning"):
            out.append(f"- **{cal['warning']}**")
        out.append("")

    grid = r.get("grid")
    if grid:
        out.append("## 成果格网")
        out.append("")
        out.append(f"- 坐标系：EPSG:{grid['epsg']}（WGS84 / UTM {grid['epsg'] % 100}{'N' if grid['epsg'] // 100 == 326 else 'S'}）")
        out.append(f"- 分辨率：{grid['gsd'] * 100:.2f} cm；大小 {grid['width']} × {grid['height']} 像元")
        out.append(f"- 左上角：E {grid['x0']:.3f}，N {grid['y0']:.3f}")
        if r.get("coverage"):
            cov = r["coverage"]
            out.append(f"- 有效覆盖：{cov['area_m2']:.0f} m²（占格网 {cov['valid_ratio'] * 100:.1f}%）")
        out.append("")

    products = r.get("products") or []
    if products:
        out.append("## 成果文件")
        out.append("")
        out.extend(f"- `{p['path']}`（{_fmt_bytes(p.get('bytes'))}）：{p.get('label', '')}" for p in products)
        out.append("")

    timings = r.get("stage_timings") or {}
    if timings:
        out.append("## 耗时")
        out.append("")
        out.extend(f"- {LABELS.get(k, k)}：{v:.1f} 秒" for k, v in timings.items())
        out.append("")

    out.append("## 处理假设")
    out.append("")
    out.extend(f"- {a}" for a in r.get("assumptions") or ASSUMPTIONS)
    out.append("")
    return "\n".join(out)
