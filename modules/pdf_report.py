from __future__ import annotations
from io import BytesIO
import math
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether


def build_pdf_report(df: pd.DataFrame, kpis: dict, charts: dict, output_path=None) -> BytesIO:
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), rightMargin=1.4*cm, leftMargin=1.4*cm, topMargin=1.3*cm, bottomMargin=1.3*cm, title="Data Studio Analytics Report", author="Data Studio")
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=21, textColor=colors.HexColor("#19365d"), alignment=TA_CENTER, spaceAfter=4)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748b"), alignment=TA_CENTER)
    heading = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#276ee8"), spaceBefore=10, spaceAfter=6)
    story = [Paragraph("Data Studio · Analytics Report", title), Paragraph(f"Generated report · {len(df):,} rows · {len(df.columns)} fields", subtitle), Spacer(1, .5*cm), Paragraph("Key performance indicators", heading)]
    labels = {"total_orders":"Total orders", "valid_orders":"Valid orders", "unique_customers":"Unique customers", "total_revenue":"Net revenue", "total_profit":"Net profit", "avg_order_value":"Average order value", "avg_rating":"Average customer rating"}
    data = [["Metric", "Value"]] + [[labels.get(k, k.replace("_", " ").title()), f"{v:,.2f}" if isinstance(v, (int, float)) and math.isfinite(v) else str(v)] for k, v in kpis.items()]
    table = Table(data, colWidths=[8*cm, 5*cm], hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#19365d")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#dbe2ec")),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f4f7fb")]),("PADDING",(0,0),(-1,-1),6)]))
    story += [table, Spacer(1, .35*cm), Paragraph("Visual summaries", heading)]
    for label, series in charts.items():
        if not series: continue
        labels_data = list(series.keys())[:12]
        values = [float(series[k]) for k in labels_data]
        fig, ax = plt.subplots(figsize=(7, 2.6), dpi=130)
        fig.patch.set_facecolor("white"); ax.set_facecolor("white")
        ax.bar(range(len(labels_data)), values, color="#4386ee", width=.68)
        ax.set_xticks(range(len(labels_data))); ax.set_xticklabels([str(x)[:18] for x in labels_data], rotation=25, ha="right", fontsize=7)
        ax.tick_params(axis="y", labelsize=7, colors="#718096"); ax.tick_params(axis="x", colors="#52627a")
        ax.spines[["top", "right", "left"]].set_visible(False); ax.spines["bottom"].set_color("#dce3ed"); ax.grid(axis="y", alpha=.18); ax.set_axisbelow(True)
        ax.set_title(str(label), loc="left", fontsize=11, color="#253650", pad=8)
        fig.tight_layout()
        image = BytesIO(); fig.savefig(image, format="png", bbox_inches="tight", facecolor="white"); plt.close(fig); image.seek(0)
        story.append(KeepTogether([Image(image, width=23*cm, height=8.2*cm, kind="proportional"), Spacer(1, .2*cm)]))
    story.extend([PageBreak(), Paragraph("Data sample · first 15 rows", heading)])
    sample = df.head(15).iloc[:, :8].copy().astype(object).where(pd.notna(df.head(15).iloc[:, :8]), "")
    sample_data = [[str(c)[:20] for c in sample.columns]] + [[str(v)[:42] for v in row] for row in sample.values.tolist()]
    widths = [24*cm / max(len(sample.columns),1)] * len(sample.columns)
    sample_table = Table(sample_data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    sample_table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#276ee8")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),6.5),("GRID",(0,0),(-1,-1),.3,colors.HexColor("#d8dee9")),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f4f7fb")]),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),4)]))
    story.append(sample_table)
    doc.build(story)
    output.seek(0)
    return output
