"""自研质量报告 PDF。

章节、表头、附图与需求样例 `0611hsl_Report.pdf` 对齐，便于对照验收。
页面上不署内部包名。数字来自本次解算，不冒充 LiMapper / Pix4D。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ms_mosaic.qa import format_gps_extrema_line, truncate_rows

REPORT_SECTIONS = (
    "概述",
    "2D关键点检测",
    "2D关键点匹配",
    "相机标定信息",
    "空中三角测量",
    "密集点云",
    "数字表面模型（DSM）",
    "正射影像",
    "基于GPS配准",
    "GPS配准列表",
    "相机位置视图",
    "空三视图",
    "重叠度视图",
    "航线信息",
    "较差航向重叠率信息",
    "较差旁向重叠率信息",
    "平均重叠率信息",
)

RED = (0.75, 0.0, 0.0)


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    name = "STSong-Light"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


def _styles():
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    font = _font()
    return {
        "title": ParagraphStyle("t", fontName=font, fontSize=22, alignment=TA_CENTER, spaceAfter=4, leading=26),
        "sub": ParagraphStyle("sub", fontName=font, fontSize=8, alignment=TA_RIGHT, textColor="#444444"),
        "h1": ParagraphStyle("h1", fontName=font, fontSize=13, spaceBefore=12, spaceAfter=8, leading=16),
        "body": ParagraphStyle("b", fontName=font, fontSize=8, alignment=TA_LEFT, leading=11),
        "cell": ParagraphStyle("c", fontName=font, fontSize=7.5, leading=10),
        "cell_c": ParagraphStyle("cc", fontName=font, fontSize=7.5, leading=10, alignment=TA_CENTER),
        "head": ParagraphStyle("h", fontName=font, fontSize=7, leading=9, alignment=TA_CENTER),
        "caption": ParagraphStyle("cap", fontName=font, fontSize=8, alignment=TA_CENTER, spaceBefore=4, spaceAfter=8),
        "warn": ParagraphStyle("w", fontName=font, fontSize=7, leading=10, textColor="#c00000"),
        "tiny": ParagraphStyle("tiny", fontName=font, fontSize=6.5, leading=8.5),
    }


def _fmt(value, nd=6) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if value != value:  # nan
            return "-"
        return f"{value:.{nd}g}"
    return str(value)


def _pct(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "-"
    return f"{float(value) * 100:.4f}%"


def _p(text, style=None):
    from reportlab.platypus import Paragraph

    return Paragraph(str(text), style or _styles()["cell"])


def _kv_table(pairs: list[tuple[str, object]], *, warn_labels: set[str] | None = None):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    s = _styles()
    warn_labels = warn_labels or set()
    data = []
    warn_rows = []
    for i, (k, v) in enumerate(pairs):
        style = s["warn"] if k in warn_labels else s["cell"]
        data.append([_p(k, s["cell"]), _p(v, style)])
        if k in warn_labels:
            warn_rows.append(i)
    t = Table(data, colWidths=[155, 360])
    cmds = [
        ("FONTNAME", (0, 0), (-1, -1), _font()),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (0, -1), colors.Color(0.96, 0.96, 0.96)),
    ]
    for r in warn_rows:
        cmds.append(("TEXTCOLOR", (1, r), (1, r), colors.Color(*RED)))
    t.setStyle(TableStyle(cmds))
    return t


def _grid_table(header: list, rows: list[list], col_widths: list[float], *, font_size: float = 6.5):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    s = _styles()
    data = [[_p(h, s["head"]) for h in header]]
    for row in rows:
        data.append([cell if hasattr(cell, "getPlainText") else _p(cell, s["tiny"]) for cell in row])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _font()),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.93, 0.93, 0.93)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def _minutes(seconds) -> str:
    try:
        return f"{float(seconds) / 60.0:.3f} [minutes]"
    except (TypeError, ValueError):
        return "-"


def _seconds(seconds) -> str:
    try:
        return f"{float(seconds):.4g} [seconds]"
    except (TypeError, ValueError):
        return "-"


def _header(canvas, doc, *, first: bool, created_cn: str):
    canvas.saveState()
    w, h = doc.pagesize
    if first:
        canvas.setFillColorRGB(0, 0, 0)
        canvas.setFont(_font(), 22)
        canvas.drawCentredString(w / 2.0, h - 42, "质量报告")
        canvas.setFont(_font(), 8)
        canvas.setFillColorRGB(0.25, 0.25, 0.25)
        canvas.drawRightString(w - 40, h - 72, f"创建于 {created_cn}")
    else:
        canvas.setFont(_font(), 8)
        canvas.setFillColorRGB(0.3, 0.3, 0.3)
        canvas.drawString(40, h - 28, "质量报告")
        canvas.drawRightString(w - 40, 24, str(doc.page))
    canvas.restoreState()


def _diamond(color: str, size: float = 8):
    """图例用的菱形色块。颜色与相机位置图里的散点一致。"""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Flowable

    class Diamond(Flowable):
        def __init__(self) -> None:
            super().__init__()
            self.width = size
            self.height = size

        def draw(self) -> None:
            c = self.canv
            c.setFillColor(HexColor(color))
            p = c.beginPath()
            p.moveTo(size / 2, 0)
            p.lineTo(size, size / 2)
            p.lineTo(size / 2, size)
            p.lineTo(0, size / 2)
            p.close()
            c.drawPath(p, fill=1, stroke=0)

    return Diamond()


def _marker_legend(items: list[tuple[str, str]]):
    """色块加中文。中文走 PDF 字体，避免嵌进 matplotlib 图后变成方框。"""
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    row = []
    widths = []
    for color, label in items:
        row.extend([_diamond(color), Paragraph(label, styles["cell"])])
        widths.extend([14, 72])
    legend = Table([row], colWidths=widths)
    legend.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    wrap = Table([[legend]])
    wrap.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    return wrap


def _maybe_image(path, width=240, height=240):
    from reportlab.platypus import Image, Spacer

    if not path:
        return Spacer(width, 12)
    p = Path(path)
    if not p.exists():
        return Spacer(width, 12)
    img = Image(str(p), width=width, height=height)
    img.hAlign = "CENTER"
    return img


def _seq_text(seq: dict) -> str:
    indices = seq.get("indices") or []
    n = seq.get("n", len(indices))
    if not indices:
        return "..."
    if n <= 12:
        shown = ", ".join(str(i) for i in indices)
    else:
        head = ", ".join(str(i) for i in indices[:6])
        tail = ", ".join(str(i) for i in indices[-5:])
        shown = f"{head}, ..., {tail}"
    return f"序列 {seq.get('id', 0)} ({n} 影像): 序号 = {shown}"


def write_quality_pdf(payload: dict, path: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    story = []

    at = payload.get("at") or {}
    dsm = payload.get("dsm") or {}
    ortho = payload.get("ortho") or {}
    timings = payload.get("timings") or {}
    overlap = payload.get("overlap") or {}
    feat = payload.get("features") or {}
    match = payload.get("matching") or {}
    dense = payload.get("dense") or {}
    figures = payload.get("figures") or {}
    created = payload.get("created_at") or datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    created_cn = payload.get("created_date_cn") or datetime.now().strftime("%Y年%m月%d日")
    n_images = payload.get("n_images")
    n_reg = payload.get("n_registered", at.get("n_images"))
    crs = payload.get("crs_label") or payload.get("crs")
    agl = payload.get("mean_agl_m", 0)

    story.append(Paragraph("概述", styles["h1"]))
    story.append(
        _kv_table(
            [
                ("工程名称", payload.get("project_name", "")),
                ("创建时间", created),
                ("影像数目", n_images),
                ("测区面积", f"{_fmt(payload.get('area_km2', 0))} 平方公里"),
                ("平均航高", f"{_fmt(agl, 4)} [米]"),
                ("地面控制点", payload.get("gcp", 0)),
                ("参考坐标系", crs),
                ("影像对齐耗时", _minutes(timings.get("at", 0))),
                ("生成密集点云耗时", _minutes(timings.get("dense", 0))),
                ("生成DSM耗时", _seconds(timings.get("dsm", 0))),
                ("生成正射影像耗时", _minutes(timings.get("ortho", 0))),
            ]
        )
    )
    story.append(Spacer(1, 10))
    thumbs = Table(
        [
            [_maybe_image(figures.get("ortho"), 230, 200), _maybe_image(figures.get("dsm"), 230, 200)],
            [
                Paragraph("Orthomosaic_pix_surf_group0", styles["caption"]),
                Paragraph(
                    "DSM<br/>色标数字是椭球高（米），由照片定位高度经空三和密集匹配算出，不是海拔。",
                    styles["caption"],
                ),
            ],
        ],
        colWidths=[250, 250],
    )
    thumbs.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(thumbs)

    story.append(Paragraph("2D关键点检测", styles["h1"]))
    poor = feat.get("poor") or []
    poor_cell = (
        f"{feat.get('n_poor', len(poor))} 差影像, " + ", ".join(poor[:12]) + (" ..." if len(poor) > 12 else "")
        if poor
        else "-"
    )
    story.append(
        _kv_table(
            [
                ("2D关键点总数", feat.get("n_keypoints")),
                ("最大影像尺度", feat.get("scale", "大")),
                ("影像关键点数最大值", feat.get("max_keypoints", 8192)),
                ("影像关键点数均值", _fmt(feat.get("mean_keypoints"), 4)),
                ("特征点最多的影像", feat.get("most", "-")),
                ("特征点最少的影像", feat.get("least", "-")),
                ("潜在较差影像", poor_cell),
            ],
            warn_labels={"潜在较差影像"} if poor else None,
        )
    )

    story.append(Paragraph("2D关键点匹配", styles["h1"]))
    seqs = match.get("sequences") or []
    seq_rows: list[tuple[str, object]] = [
        ("连接点数目", match.get("n_tracks", at.get("n_points"))),
        ("像对筛选模式", match.get("pair_mode", "一般")),
        ("单张影像最大两视图连接点数量", match.get("max_two_view")),
        ("单张影像平均连接点数量", _fmt(match.get("mean_tracks", at.get("mean_obs_per_image")), 4)),
        ("连接点最多的影像", match.get("most", "-")),
        ("连接点最少的影像", match.get("least", "-")),
    ]
    if seqs:
        show = seqs
        if len(seqs) > 10:
            show = seqs[:5] + [{"id": f"{seqs[5].get('id', 5)}..{seqs[-4].get('id', len(seqs)-4)}", "indices": [], "n": 0}] + seqs[-3:]
        for seq in show:
            if not seq.get("indices") and seq.get("n") == 0:
                seq_rows.append((f"关联影像序列 {seq.get('id')}", "..."))
            else:
                seq_rows.append((f"关联影像序列 {seq.get('id', 0)}", _seq_text(seq)))
    story.append(_kv_table(seq_rows))

    story.append(Paragraph("相机标定信息", styles["h1"]))
    cams = payload.get("cameras") or {}
    cam_header = ["影像组号", "相机类型", "影像尺寸", "焦距", "像主点", "K1,K2,K3", "P1,P2", "B1,B2", "影像", "均方根误差"]
    cam_rows = []
    for name, c in cams.items():
        g = c.get("group")
        if g is None:
            try:
                g = list(cams).index(name)
            except ValueError:
                g = 0
        n_all = c.get("n_images") or 0
        n_ok = c.get("n_registered")
        if n_ok is None:
            n_ok = n_all
        cam_rows.append(
            [
                f"组 {g}",
                c.get("model", payload.get("camera_type", "MAX-S800")),
                f"[{c.get('width', '-')}x{c.get('height', '-')}]",
                f"{_fmt(c.get('f'), 6)} [像素]",
                f"{_fmt(c.get('cx'), 6)}, {_fmt(c.get('cy'), 6)} [像素]",
                f"{_fmt(c.get('k1'), 6)}, {_fmt(c.get('k2'), 6)}, {_fmt(c.get('k3'), 6)}",
                f"{_fmt(c.get('p1'), 6)}, {_fmt(c.get('p2'), 6)}",
                f"{_fmt(c.get('b1'), 6)}, {_fmt(c.get('b2'), 6)}",
                f"{n_ok}/{n_all}" if n_all else str(n_ok),
                f"{_fmt(c.get('rms_px'), 6)} [像素]",
            ]
        )
    if cam_rows:
        story.append(
            _grid_table(cam_header, cam_rows, [42, 52, 58, 52, 72, 78, 52, 52, 36, 58], font_size=6)
        )

    story.append(Paragraph("空中三角测量", styles["h1"]))
    unreg = payload.get("unregistered") or []
    unreg_text = f"{len(unreg)}({', '.join(str(i) for i in unreg[:24])})" if unreg else "0"
    story.append(
        _kv_table(
            [
                ("已注册影像", f"{n_reg}/{n_images}"),
                ("主波段", payload.get("primary_band", "组0-")),
                ("地理参考模式", "基于GPS配准"),
                ("外点阈值（像素）", 6),
                ("调整内参", "f, cx, cy, k1, k2, k3, b1, b2, p1, p2"),
                ("调整外参", "R, T"),
                ("稀疏3D点数量", at.get("n_points")),
                ("2D观测值数量", at.get("n_observations")),
                ("单张影像平均2D观测值数量", _fmt(at.get("mean_obs_per_image"), 4)),
                ("3D稀疏点最多的影像", payload.get("sparse_most", "-")),
                ("3D稀疏点最少的影像", payload.get("sparse_least", "-")),
                ("重投影误差均值", f"{_fmt(at.get('mean_reprojection_px'), 6)} [像素]"),
                ("均方根重投影误差", f"{_fmt(at.get('rms_reprojection_px'), 6)} [像素]"),
                ("未注册影像索引列表", unreg_text),
            ],
            warn_labels={"未注册影像索引列表"} if unreg else None,
        )
    )

    story.append(Paragraph("密集点云", styles["h1"]))
    story.append(
        _kv_table(
            [
                ("密集点数量", dense.get("n_valid")),
                ("质量", "高"),
                ("类型", "2.5D"),
                ("平均点间距", f"{_fmt(dense.get('gsd', dsm.get('gsd')))} m"),
            ]
        )
    )

    story.append(Paragraph("数字表面模型（DSM）", styles["h1"]))
    story.append(_kv_table([("分辨率", f"{_fmt(dsm.get('gsd'))} m")]))

    story.append(Paragraph("正射影像", styles["h1"]))
    story.append(
        _kv_table(
            [
                ("模式", ortho.get("mode", "基于DSM逐像素拼接")),
                ("融合层级", ortho.get("blend", "中")),
                ("颜色校正", ortho.get("color_correction", "禁用")),
                ("最大倾斜角", f"{ortho.get('max_tilt_deg', 60)} 度"),
                ("分辨率", f"{_fmt(ortho.get('gsd'))} m"),
            ]
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("基于GPS配准", styles["h1"]))
    story.append(Paragraph("单位：m", styles["body"]))
    story.append(Spacer(1, 4))
    n_gps = at.get("n_gps", at.get("n_images"))
    gps_head = ["GPS参考点数目", "配准均方根误差", "均方根误差-X", "均方根误差-Y", "均方根误差-Z"]
    gps_sum = [[
        str(n_gps),
        _fmt(at.get("gps_rmse_m"), 5),
        _fmt(at.get("gps_rmse_x_m"), 6),
        _fmt(at.get("gps_rmse_y_m"), 6),
        _fmt(at.get("gps_rmse_z_m"), 6),
    ]]
    story.append(_grid_table(gps_head, gps_sum, [100, 100, 100, 100, 100], font_size=8))
    story.append(Spacer(1, 8))
    extrema = payload.get("gps_extrema") or {}
    labels = [
        ("X轴配准误差最小值", "min_x"),
        ("Y轴配准误差最小值", "min_y"),
        ("Z轴配准误差最小值", "min_z"),
        ("配准误差最小值", "min_err"),
        ("X轴配准误差最大值", "max_x"),
        ("Y轴配准误差最大值", "max_y"),
        ("Z轴配准误差最大值", "max_z"),
        ("配准误差最大值", "max_err"),
    ]
    ext_pairs = []
    for lab, key in labels:
        row = extrema.get(key)
        ext_pairs.append((lab, format_gps_extrema_line(row) if row else "-"))
    if ext_pairs:
        story.append(_kv_table(ext_pairs))

    story.append(Paragraph("GPS配准列表", styles["h1"]))
    story.append(Paragraph("单位：m", styles["body"]))
    hist = payload.get("gps_histogram") or []
    if hist:
        hist_header = ["误差范围", "误差", "dX", "dY", "dZ"]
        hist_rows = [
            [
                b["label"],
                f"{b['error']:.6g}%",
                f"{b['dx']:.6g}%",
                f"{b['dy']:.6g}%",
                f"{b['dz']:.6g}%",
            ]
            for b in hist
        ]
        story.append(_grid_table(hist_header, hist_rows, [90, 90, 90, 90, 90], font_size=7))
        story.append(Spacer(1, 8))
    gps_rows = payload.get("gps_rows") or []
    if gps_rows:
        list_header = ["序号", "初始-X", "初始-Y", "初始-Z", "优化-X", "优化-Y", "优化-Z", "误差", "dX", "dY", "dZ"]
        list_rows = []
        for row in truncate_rows(gps_rows, head=12, tail=10):
            if row is None:
                list_rows.append([".."] * 11)
                continue
            list_rows.append(
                [
                    str(row["index"]),
                    f"{row['gps0'][0]:.4f}",
                    f"{row['gps0'][1]:.3f}",
                    f"{row['gps0'][2]:.7f}",
                    f"{row['gps1'][0]:.4f}",
                    f"{row['gps1'][1]:.3f}",
                    f"{row['gps1'][2]:.7f}",
                    f"{row['error']:.5g}",
                    f"{row['dx']:.6g}",
                    f"{row['dy']:.6g}",
                    f"{row['dz']:.6g}",
                ]
            )
        story.append(
            _grid_table(list_header, list_rows, [32, 52, 58, 48, 52, 58, 48, 40, 40, 40, 40], font_size=6)
        )

    story.append(PageBreak())
    story.append(KeepTogether([
        Paragraph("相机位置视图", styles["h1"]),
        _maybe_image(figures.get("camera_pos"), 420, 420),
        _marker_legend([("#1f4e9e", "初始POS"), ("#2ca02c", "优化POS")]),
        Paragraph("相机位置视图", styles["caption"]),
    ]))
    story.append(KeepTogether([
        Paragraph("空三视图", styles["h1"]),
        _maybe_image(figures.get("at_view"), 420, 420),
        Paragraph("可视区域　　3D稀疏点　　相机位置", styles["caption"]),
        Paragraph("空三视图", styles["caption"]),
    ]))
    story.append(KeepTogether([
        Paragraph("重叠度视图", styles["h1"]),
        _maybe_image(figures.get("overlap"), 420, 420),
        Paragraph("1　2　3　4　5　6　7　8　9　≥10", styles["caption"]),
        Paragraph("重叠度视图", styles["caption"]),
    ]))

    story.append(PageBreak())
    story.append(Paragraph("航线信息", styles["h1"]))
    strips = payload.get("strips") or []
    if strips:
        strip_header = ["航线序列(组号)", "影像列表"]
        strip_rows = []
        for i, names in enumerate(strips):
            shown = ", ".join(names[:18])
            if len(names) > 18:
                shown += ", ..."
            strip_rows.append([f"航线 {i} (组0)", shown])
        story.append(_grid_table(strip_header, strip_rows, [90, 410], font_size=7))
    else:
        story.append(Paragraph("（按主波段时间序，未单独切航线）", styles["body"]))

    def _overlap_table(title: str, items: list[dict]):
        story.append(Paragraph(title, styles["h1"]))
        if not items:
            story.append(Paragraph("无", styles["body"]))
            return
        header = ["序号", "影像信息", "重叠率"]
        rows = []
        for k, rec in enumerate(items):
            rows.append(
                [
                    str(k),
                    f"序号 = {rec.get('a')},{rec.get('name_a')} - 序号 = {rec.get('b')},{rec.get('name_b')}",
                    _pct(rec.get("overlap")),
                ]
            )
        story.append(_grid_table(header, rows, [40, 375, 90], font_size=7))

    _overlap_table("较差航向重叠率信息", overlap.get("poor_forward") or [])
    _overlap_table("较差旁向重叠率信息", overlap.get("poor_side") or [])

    story.append(Paragraph("平均重叠率信息", styles["h1"]))
    fwd, side = overlap.get("forward"), overlap.get("side")
    story.append(
        _kv_table(
            [
                ("平均航向重叠率", _pct(fwd) if isinstance(fwd, (int, float)) else "-"),
                ("平均旁向重叠率", _pct(side) if isinstance(side, (int, float)) else "-"),
            ]
        )
    )

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        title="质量报告",
        author="高光谱拼图",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=28 * mm,
        bottomMargin=16 * mm,
    )

    def on_first(canvas, doc_):
        _header(canvas, doc_, first=True, created_cn=created_cn)

    def on_later(canvas, doc_):
        _header(canvas, doc_, first=False, created_cn=created_cn)

    doc.build(story, onFirstPage=on_first, onLaterPages=on_later)
    return path
