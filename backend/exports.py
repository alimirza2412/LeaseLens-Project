"""Report exporters: Excel (.xlsx) and PDF.

Both take a plain-dict `LeaseReport` (as produced by `LeaseReport.model_dump()`)
and return raw bytes ready to stream to the browser. Keeping this on the backend
means the HTML/JS frontend just downloads a blob — no client-side document
libraries needed.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ABSTRACT_LABELS = {
    "tenant_name": "Tenant",
    "landlord_name": "Landlord",
    "property_address": "Property Address",
    "lease_commencement_date": "Commencement Date",
    "lease_expiration_date": "Expiration Date",
    "monthly_base_rent": "Monthly Base Rent",
    "annual_rent_escalation": "Annual Rent Escalation",
    "security_deposit": "Security Deposit",
    "cam_charges_type": "CAM / Lease Type",
    "renewal_options": "Renewal Options",
    "early_termination_rights": "Early Termination Rights",
    "page_citations": "Source Pages",
}

# Soft, light severity palette (matches the frontend).
_SEV_FILL = {
    "HIGH": colors.HexColor("#fbe4e2"),
    "MEDIUM": colors.HexColor("#fcf1d8"),
    "LOW": colors.HexColor("#e5f1e4"),
}
_SEV_TEXT = {
    "HIGH": colors.HexColor("#8f2f27"),
    "MEDIUM": colors.HexColor("#8a6516"),
    "LOW": colors.HexColor("#2f6a37"),
}


def _fmt_money(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"${v:,.2f}" if v else "Not specified"


def abstract_rows(abstract: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, display_value) pairs in a fixed, human-friendly order."""
    rows: list[tuple[str, str]] = []
    for key, label in ABSTRACT_LABELS.items():
        val = abstract.get(key)
        if key in ("monthly_base_rent", "security_deposit"):
            display = _fmt_money(val)
        elif key == "page_citations":
            display = ", ".join(str(p) for p in (val or [])) or "—"
        else:
            display = str(val) if val not in (None, "") else "Not specified"
        rows.append((label, display))
    return rows


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #
def build_excel(report: dict[str, Any]) -> bytes:
    abstract = report.get("abstract", {})
    risks = report.get("risks", [])

    abstract_df = pd.DataFrame(abstract_rows(abstract), columns=["Field", "Value"])

    if risks:
        risk_df = pd.DataFrame(
            [
                {
                    "Severity": r.get("severity", ""),
                    "Issue": r.get("issue_title", ""),
                    "Explanation": r.get("explanation", ""),
                    "Page": r.get("page_number", ""),
                    "Section / Clause": r.get("section_clause", ""),
                }
                for r in risks
            ]
        )
    else:
        risk_df = pd.DataFrame(
            [{"Severity": "—", "Issue": "No notable risks detected",
              "Explanation": "", "Page": "", "Section / Clause": ""}]
        )

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        abstract_df.to_excel(writer, sheet_name="Lease Abstract", index=False)
        risk_df.to_excel(writer, sheet_name="Risk Matrix", index=False)
    buffer.seek(0)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def build_pdf(report: dict[str, Any]) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title="LeaseLens — Lease Abstract & Risk Report",
    )

    base = getSampleStyleSheet()
    ink = colors.HexColor("#1f2328")
    muted = colors.HexColor("#6f7580")
    h1 = ParagraphStyle("h1", parent=base["Title"], fontSize=20, textColor=ink,
                        spaceAfter=2, alignment=0)
    sub = ParagraphStyle("sub", parent=base["Normal"], fontSize=9,
                         textColor=muted, spaceAfter=14)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, textColor=ink,
                        spaceBefore=6, spaceAfter=8)
    cell = ParagraphStyle("cell", parent=base["Normal"], fontSize=9.5, leading=13,
                          textColor=ink)

    def p(text: str, style=cell) -> Paragraph:
        return Paragraph(escape(str(text)), style)

    story: list[Any] = []
    story.append(Paragraph("LeaseLens", h1))
    story.append(Paragraph("Lease Abstract &amp; Risk Report", h2))
    story.append(Paragraph(
        f"Source: {escape(str(report.get('source', '')))} &nbsp;·&nbsp; "
        f"Document ID: {escape(str(report.get('doc_id', '')))}", sub))

    # ---- Abstract table ----
    story.append(Paragraph("Extracted Lease Terms", h2))
    rows = [[p("Field"), p("Value")]]
    for label, value in abstract_rows(report.get("abstract", {})):
        rows.append([p(label), p(value)])
    t = Table(rows, colWidths=[55 * mm, 110 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1ec")),
        ("TEXTCOLOR", (0, 0), (-1, 0), ink),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e2dc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#faf9f5")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # ---- Risk matrix ----
    story.append(Paragraph("Risk Matrix", h2))
    risks = report.get("risks", [])
    if not risks:
        story.append(Paragraph("No notable tenant-side risks detected.", cell))
    else:
        header = [p("Severity"), p("Issue"), p("Explanation"), p("Pg"), p("Clause")]
        rrows = [header]
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1ec")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e2dc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]
        for i, r in enumerate(risks, start=1):
            sev = str(r.get("severity", "MEDIUM")).upper()
            sev_style = ParagraphStyle(
                f"sev{i}", parent=cell,
                textColor=_SEV_TEXT.get(sev, ink), fontName="Helvetica-Bold")
            rrows.append([
                Paragraph(escape(sev), sev_style),
                p(r.get("issue_title", "")),
                p(r.get("explanation", "")),
                p(r.get("page_number", "")),
                p(r.get("section_clause", "")),
            ])
            style_cmds.append(
                ("BACKGROUND", (0, i), (0, i), _SEV_FILL.get(sev, colors.white)))
        rt = Table(
            rrows,
            colWidths=[20 * mm, 34 * mm, 71 * mm, 12 * mm, 28 * mm],
            repeatRows=1,
        )
        rt.setStyle(TableStyle(style_cmds))
        story.append(rt)

    doc.build(story)
    return buffer.getvalue()
