#!/usr/bin/env python3
"""Generate customer-manual.pdf from customer-manual.md (reportlab + CJK font)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
MD = HERE / "customer-manual.md"
PDF = HERE / "customer-manual.pdf"


def find_font() -> tuple[str, str]:
    candidates = [
        (Path("/fonts/light.ttc"), Path("/fonts/medium.ttc")),
        (Path("/tmp/st_light.ttc"), Path("/tmp/st_medium.ttc")),
        (Path("/System/Library/Fonts/STHeiti Light.ttc"), Path("/System/Library/Fonts/STHeiti Medium.ttc")),
    ]
    for light, medium in candidates:
        if light.exists() and medium.exists():
            return str(light), str(medium)
    raise SystemExit("未找到中文字体（STHeiti）")


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(s: str) -> str:
    html = esc(s)
    html = re.sub(r"`([^`]+)`", r"<font face='Courier' size='8'>\1</font>", html)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", html)
    return html


def main() -> None:
    light, medium = find_font()
    pdfmetrics.registerFont(TTFont("CN", light, subfontIndex=0))
    pdfmetrics.registerFont(TTFont("CN-Bold", medium, subfontIndex=0))

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", fontName="CN-Bold", fontSize=18, leading=26, spaceAfter=12, spaceBefore=6))
    styles.add(ParagraphStyle(name="H2", fontName="CN-Bold", fontSize=14, leading=22, spaceAfter=8, spaceBefore=14))
    styles.add(ParagraphStyle(name="H3", fontName="CN-Bold", fontSize=12, leading=18, spaceAfter=6, spaceBefore=10))
    styles.add(ParagraphStyle(name="BodyCN", fontName="CN", fontSize=10, leading=16, spaceAfter=6))
    styles.add(
        ParagraphStyle(
            name="CodeCN",
            fontName="Courier",
            fontSize=8.5,
            leading=12,
            backColor=colors.Color(0.95, 0.95, 0.96),
            leftIndent=4,
            rightIndent=4,
            spaceAfter=8,
            spaceBefore=4,
        )
    )
    styles.add(ParagraphStyle(name="LiCN", fontName="CN", fontSize=10, leading=16, leftIndent=14, spaceAfter=2))
    styles.add(
        ParagraphStyle(name="CenterCN", fontName="CN", fontSize=9, leading=14, alignment=TA_CENTER, textColor=colors.grey)
    )

    text = MD.read_text(encoding="utf-8")
    doc = SimpleDocTemplate(
        str(PDF),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title="高光谱拼图 · 客户操作手册",
    )
    story: list = []
    lines = text.splitlines()
    i = 0
    in_code = False
    code_buf: list[str] = []
    table_buf: list[str] = []

    def flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        rows = []
        for row in table_buf:
            if re.match(r"^\|?\s*-+", row):
                continue
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            rows.append([Paragraph(esc(c), styles["BodyCN"]) for c in cells])
        if rows:
            t = Table(rows, hAlign="LEFT")
            t.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.Color(0.75, 0.78, 0.82)),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.93, 0.95, 0.97)),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.append(Spacer(1, 6))
            story.append(t)
            story.append(Spacer(1, 8))
        table_buf = []

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code_buf) or " ", styles["CodeCN"]))
                code_buf = []
                in_code = False
            else:
                flush_table()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        if line.strip().startswith("|"):
            table_buf.append(line)
            i += 1
            continue
        flush_table()
        if line.strip() == "---":
            story.append(Spacer(1, 8))
        elif line.startswith("# "):
            story.append(Paragraph(esc(line[2:].strip()), styles["H1"]))
        elif line.startswith("## "):
            story.append(Paragraph(esc(line[3:].strip()), styles["H2"]))
        elif line.startswith("### "):
            story.append(Paragraph(esc(line[4:].strip()), styles["H3"]))
        elif re.match(r"^\d+\.\s", line.strip()) or line.strip().startswith("- "):
            story.append(Paragraph("• " + esc(re.sub(r"^(\d+\.\s|- )", "", line.strip())), styles["LiCN"]))
        elif line.strip() == "":
            story.append(Spacer(1, 4))
        elif line.strip().startswith("*") and line.strip().endswith("*"):
            story.append(Paragraph(esc(line.strip().strip("*")), styles["CenterCN"]))
        else:
            story.append(Paragraph(inline(line), styles["BodyCN"]))
        i += 1

    flush_table()
    doc.build(story)
    print(f"wrote {PDF} ({PDF.stat().st_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
