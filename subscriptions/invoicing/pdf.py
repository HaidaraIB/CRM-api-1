"""
Subscription invoice PDF (ReportLab Platypus only).
"""
from __future__ import annotations

import logging
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from subscriptions.invoicing.branding import IssuerBranding
from subscriptions.invoicing.view_model import InvoiceView

logger = logging.getLogger(__name__)

_SLATE = colors.HexColor("#374151")
_SLATE_LIGHT = colors.HexColor("#6b7280")
_ACCENT = colors.HexColor("#4f46e5")
_BORDER = colors.HexColor("#e5e7eb")
_BG_MUTED = colors.HexColor("#f9fafb")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title_left": ParagraphStyle(
            name="InvTitleLeft",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=28,
            textColor=_ACCENT,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "meta_left": ParagraphStyle(
            name="InvMetaLeft",
            parent=base["Normal"],
            fontSize=10,
            textColor=_SLATE_LIGHT,
            alignment=TA_LEFT,
            leading=14,
            spaceAfter=3,
        ),
        "h3": ParagraphStyle(
            name="InvH3",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=_SLATE_LIGHT,
            spaceAfter=6,
            leading=10,
        ),
        "body": ParagraphStyle(
            name="InvBody",
            parent=base["Normal"],
            fontSize=10,
            textColor=_SLATE,
            leading=14,
        ),
        "small": ParagraphStyle(
            name="InvSmall",
            parent=base["Normal"],
            fontSize=8,
            textColor=_SLATE_LIGHT,
            leading=11,
        ),
        "notes_title": ParagraphStyle(
            name="InvNotesTitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=_ACCENT,
            spaceAfter=4,
        ),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _vstack(flowables, col_w) -> Table:
    return Table([[f] for f in flowables], colWidths=[col_w])


def _issuer_block_paras(view: InvoiceView, col_w, styles: dict) -> list[Paragraph]:
    out: list[Paragraph] = [_p("FROM", styles["h3"])]
    lines = []
    if view.issuer_name:
        lines.append(view.issuer_name)
    lines.extend(view.issuer_address_lines)
    bits = []
    if view.issuer_email:
        bits.append(view.issuer_email)
    if view.issuer_phone:
        bits.append(view.issuer_phone)
    if bits:
        lines.append(" · ".join(bits))
    if view.issuer_tax_id:
        lines.append(f"Tax ID: {view.issuer_tax_id}")
    if not lines:
        lines = [view.platform_name]
    out.extend(_p(x, styles["body"]) for x in lines)
    return out


def _bill_to_paras(view: InvoiceView, styles: dict) -> list[Paragraph]:
    out = [_p("BILL TO", styles["h3"])]
    out.append(_p(view.company_name, styles["body"]))
    return out


def _header_table(view: InvoiceView, branding: IssuerBranding, content_width) -> Table:
    """Top-left: optional logo, then INVOICE on its own line, then invoice # and dates (each on a new line)."""
    styles = _styles()
    title_left = styles["title_left"]
    meta_left = styles["meta_left"]
    rows: list = []

    if branding.logo_bytes and branding.logo_mime:
        try:
            bio = BytesIO(branding.logo_bytes)
            img = Image(bio)
            img.drawHeight = 14 * mm
            img.drawWidth = img.imageWidth * (img.drawHeight / img.imageHeight)
            if img.drawWidth > 55 * mm:
                img.drawWidth = 55 * mm
                img.drawHeight = img.imageHeight * (img.drawWidth / img.imageWidth)
            rows.append([img])
            rows.append([Spacer(1, 2 * mm)])
        except Exception as e:
            logger.warning("Invoice PDF logo skipped: %s", e)

    rows.append([_p("INVOICE", title_left)])
    rows.append([_p(f"Invoice # {view.invoice_number}", meta_left)])
    if view.issue_date_str:
        rows.append([_p(f"Date: {view.issue_date_str}", meta_left)])
    if view.due_date_str:
        rows.append([_p(f"Due: {view.due_date_str}", meta_left)])

    t = Table(rows, colWidths=[content_width])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return t


def _parties_table(view: InvoiceView, content_width) -> Table:
    styles = _styles()
    half = content_width * 0.5
    left = _vstack(_issuer_block_paras(view, half, styles), half)
    right = _vstack(_bill_to_paras(view, styles), half)
    t = Table([[left, right]], colWidths=[half, half])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BG_MUTED),
                ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                ("LINEAFTER", (0, 0), (0, 0), 0.5, _BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return t


def _line_items_table(view: InvoiceView, content_width) -> Table:
    styles = _styles()
    # Paragraph text color comes from the style; table TEXTCOLOR does not override it.
    hdr_desc = ParagraphStyle(
        name="LineHdrDesc",
        parent=styles["h3"],
        textColor=colors.white,
        alignment=TA_LEFT,
    )
    hdr_amt = ParagraphStyle(
        name="LineHdrAmt",
        parent=styles["h3"],
        textColor=colors.white,
        alignment=TA_RIGHT,
    )
    amt_b = ParagraphStyle(name="AmtB", parent=styles["body"], alignment=TA_RIGHT)
    hdr = [_p("Description", hdr_desc), _p("Amount", hdr_amt)]
    row = [_p(view.line_item_description, styles["body"]), _p(view.money_display, amt_b)]
    t = Table([hdr, row], colWidths=[content_width * 0.68, content_width * 0.32])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, _BORDER),
            ]
        )
    )
    return t


def _totals_table(view: InvoiceView, content_width) -> Table:
    styles = _styles()
    w = min(120 * mm, content_width * 0.42)
    tot_v = ParagraphStyle(
        name="TotV",
        parent=styles["body"],
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_ACCENT,
        alignment=TA_RIGHT,
    )
    tot_l = ParagraphStyle(name="TotL", parent=styles["body"], fontName="Helvetica-Bold")
    rows = [
        [_p("Subtotal", styles["body"]), _p(view.subtotal_display, ParagraphStyle(name="T1", parent=styles["body"], alignment=TA_RIGHT))],
        [_p("Total", tot_l), _p(view.total_display, tot_v)],
    ]
    t = Table(rows, colWidths=[w * 0.45, w * 0.55], hAlign="RIGHT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BG_MUTED),
                ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEABOVE", (0, 1), (-1, 1), 1.5, _ACCENT),
            ]
        )
    )
    return t


def _status_pill(view: InvoiceView) -> Table:
    styles = _styles()
    inner = Paragraph(
        f"<b>{escape(view.status_label)}</b>",
        ParagraphStyle(name="pill", parent=styles["body"], fontSize=9, textColor=colors.HexColor(view.status_fg)),
    )
    t = Table([[inner]], colWidths=[28 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(view.status_bg)),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _status_row(view: InvoiceView, content_width) -> Table:
    styles = _styles()
    t = Table(
        [[_p("Payment status", styles["small"]), _status_pill(view)]],
        colWidths=[content_width * 0.22, content_width * 0.78],
    )
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def _notes_flowables(view: InvoiceView, content_w) -> list:
    styles = _styles()
    out: list = [Spacer(1, 2 * mm), _status_row(view, content_w), Spacer(1, 4 * mm)]
    note_cells: list = []
    if view.footer_text:
        note_cells.append(_p("Notes", styles["notes_title"]))
        note_cells.append(_p(view.footer_text, styles["body"]))
    if view.payment_instructions:
        note_cells.append(_p("Payment instructions", styles["notes_title"]))
        note_cells.append(_p(view.payment_instructions, styles["small"]))
    if note_cells:
        inner = _vstack(note_cells, content_w - 24)
        wrap = Table([[inner]], colWidths=[content_w])
        wrap.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                    ("BACKGROUND", (0, 0), (-1, -1), _BG_MUTED),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )
        out.append(wrap)
    out.append(Spacer(1, 4 * mm))
    out.append(
        _p(
            f"Thank you for your business.\nIssued via {view.platform_name}.",
            styles["small"],
        )
    )
    return out


def render_invoice_pdf(invoice) -> bytes:
    """
    Render invoice as PDF bytes (English labels).
    """
    branding = IssuerBranding.load()
    view = InvoiceView.build(invoice, branding, status_lang="en")

    buf = BytesIO()
    lm = rm = 18 * mm
    tm = bm = 16 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=lm,
        rightMargin=rm,
        topMargin=tm,
        bottomMargin=bm,
        title=f"Invoice {view.invoice_number}",
        author=view.issuer_name or view.platform_name,
    )
    content_w = A4[0] - lm - rm
    story = [
        _header_table(view, branding, content_w),
        Spacer(1, 6 * mm),
        _parties_table(view, content_w),
        Spacer(1, 5 * mm),
        _line_items_table(view, content_w),
        Spacer(1, 4 * mm),
        _totals_table(view, content_w),
        *_notes_flowables(view, content_w),
    ]
    doc.build(story)
    data = buf.getvalue()
    if not data.startswith(b"%PDF"):
        raise RuntimeError("PDF generation produced invalid output")
    return data


def render_invoice_pdf_bytes(invoice, language: str | None = None) -> bytes:
    """API-compatible wrapper; ``language`` is ignored (PDF is English-only)."""
    return render_invoice_pdf(invoice)
