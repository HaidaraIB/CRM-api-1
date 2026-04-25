"""
Subscription invoice PDF (ReportLab Platypus only).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from subscriptions.invoicing.branding import IssuerBranding
from subscriptions.invoicing.view_model import InvoiceView

logger = logging.getLogger(__name__)

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_NOTO_REGISTERED = False
_use_unicode_invoice_font: ContextVar[bool] = ContextVar(
    "use_unicode_invoice_font", default=False
)

_SLATE = colors.HexColor("#374151")
_SLATE_LIGHT = colors.HexColor("#6b7280")
_ACCENT = colors.HexColor("#4f46e5")
_BORDER = colors.HexColor("#e5e7eb")
_BG_MUTED = colors.HexColor("#f9fafb")


def _has_arabic_script(s: str) -> bool:
    for c in s:
        o = ord(c)
        if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0x08A0 <= o <= 0x08FF or 0xFB50 <= o <= 0xFDFF:
            return True
    return False


def _needs_arabic_pdf_font(invoice, branding: IssuerBranding) -> bool:
    parts = [
        getattr(invoice, "company_name", "") or "",
        getattr(invoice, "line_description", "") or "",
        getattr(invoice, "plan_name", "") or "",
        branding.issuer_name,
        branding.issuer_address,
        branding.footer_text,
        branding.payment_instructions,
    ]
    return any(_has_arabic_script(p) for p in parts)


def _invoice_pdf_font_names() -> tuple[str, str]:
    """
    (regular, bold) for Paragraph styles.
    Helvetica for Latin-only invoices (smaller PDF, better text extraction).
    Bundled Noto Sans Arabic when any field contains Arabic script.
    """
    global _NOTO_REGISTERED
    if not _use_unicode_invoice_font.get():
        return ("Helvetica", "Helvetica-Bold")

    reg_path = _FONT_DIR / "NotoSansArabic-Regular.ttf"
    bold_path = _FONT_DIR / "NotoSansArabic-Bold.ttf"
    fallback = ("Helvetica", "Helvetica-Bold")
    if not reg_path.is_file():
        logger.warning("Invoice PDF font missing (Arabic will show boxes): %s", reg_path)
        return fallback
    if not _NOTO_REGISTERED:
        try:
            rn = "InvNotoArabic"
            pdfmetrics.registerFont(TTFont(rn, str(reg_path), asciiReadable=True))
            if bold_path.is_file():
                bn = "InvNotoArabic-Bold"
                pdfmetrics.registerFont(TTFont(bn, str(bold_path), asciiReadable=True))
            _NOTO_REGISTERED = True
        except Exception as e:
            logger.warning("Invoice PDF font registration failed: %s", e)
            return fallback
    bn = "InvNotoArabic-Bold" if bold_path.is_file() else "InvNotoArabic"
    return ("InvNotoArabic", bn)


def _shape_mixed_text(s: str) -> str:
    """Reshape + bidi for Arabic segments so PDF order matches natural reading."""
    if not s or not _has_arabic_script(s):
        return s
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return "\n".join(
            get_display(arabic_reshaper.reshape(line)) for line in s.split("\n")
        )
    except Exception:
        return s


def _styles():
    """
    Helvetica for fixed English UI (ReportLab + Noto Sans Arabic subset drops Latin letters).
    Noto (``body`` / ``small``) only for user-supplied strings that contain Arabic script.
    """
    ct_r, ct_b = _invoice_pdf_font_names()
    ui_r, ui_b = "Helvetica", "Helvetica-Bold"
    base = getSampleStyleSheet()
    return {
        "title_left": ParagraphStyle(
            name="InvTitleLeft",
            parent=base["Normal"],
            fontName=ui_b,
            fontSize=22,
            leading=28,
            textColor=_ACCENT,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "meta_left": ParagraphStyle(
            name="InvMetaLeft",
            parent=base["Normal"],
            fontName=ui_r,
            fontSize=10,
            textColor=_SLATE_LIGHT,
            alignment=TA_LEFT,
            leading=14,
            spaceAfter=3,
        ),
        "h3": ParagraphStyle(
            name="InvH3",
            parent=base["Normal"],
            fontName=ui_b,
            fontSize=8,
            textColor=_SLATE_LIGHT,
            spaceAfter=6,
            leading=10,
        ),
        "body": ParagraphStyle(
            name="InvBody",
            parent=base["Normal"],
            fontName=ct_r,
            fontSize=10,
            textColor=_SLATE,
            leading=14,
        ),
        "body_ui": ParagraphStyle(
            name="InvBodyUI",
            parent=base["Normal"],
            fontName=ui_r,
            fontSize=10,
            textColor=_SLATE,
            leading=14,
        ),
        "small": ParagraphStyle(
            name="InvSmall",
            parent=base["Normal"],
            fontName=ct_r,
            fontSize=8,
            textColor=_SLATE_LIGHT,
            leading=11,
        ),
        "small_ui": ParagraphStyle(
            name="InvSmallUI",
            parent=base["Normal"],
            fontName=ui_r,
            fontSize=8,
            textColor=_SLATE_LIGHT,
            leading=11,
        ),
        "notes_title": ParagraphStyle(
            name="InvNotesTitle",
            parent=base["Normal"],
            fontName=ui_b,
            fontSize=9,
            textColor=_ACCENT,
            spaceAfter=4,
        ),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    shaped = _shape_mixed_text(text)
    return Paragraph(escape(shaped).replace("\n", "<br/>"), style)


def _p_field(text: str, styles: dict) -> Paragraph:
    """User / issuer text: Noto only when invoice uses Arabic and this line has Arabic."""
    if not _use_unicode_invoice_font.get():
        return _p(text, styles["body"])
    st = styles["body"] if _has_arabic_script(text) else styles["body_ui"]
    return _p(text, st)


def _p_field_small(text: str, styles: dict) -> Paragraph:
    if not _use_unicode_invoice_font.get():
        return _p(text, styles["small"])
    st = styles["small"] if _has_arabic_script(text) else styles["small_ui"]
    return _p(text, st)


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
    out.extend(_p_field(x, styles) for x in lines)
    return out


def _bill_to_paras(view: InvoiceView, styles: dict) -> list[Paragraph]:
    out = [_p("BILL TO", styles["h3"])]
    out.append(_p_field(view.company_name, styles))
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
    amt_b = ParagraphStyle(name="AmtB", parent=styles["body_ui"], alignment=TA_RIGHT)
    hdr = [_p("Description", hdr_desc), _p("Amount", hdr_amt)]
    row = [
        _p_field(view.line_item_description, styles),
        _p(view.money_display, amt_b),
    ]
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
    bui = styles["body_ui"]
    w = min(120 * mm, content_width * 0.42)
    tot_v = ParagraphStyle(
        name="TotV",
        parent=bui,
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_ACCENT,
        alignment=TA_RIGHT,
    )
    tot_l = ParagraphStyle(name="TotL", parent=bui, fontName="Helvetica-Bold")
    t1 = ParagraphStyle(name="T1", parent=bui, alignment=TA_RIGHT)
    rows = [
        [_p("Subtotal", bui), _p(view.subtotal_display, t1)],
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
        escape(_shape_mixed_text(view.status_label)),
        ParagraphStyle(
            name="pill",
            parent=styles["body_ui"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=colors.HexColor(view.status_fg),
        ),
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
        [[_p("Payment status", styles["small_ui"]), _status_pill(view)]],
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
        note_cells.append(_p_field(view.footer_text, styles))
    if view.payment_instructions:
        note_cells.append(_p("Payment instructions", styles["notes_title"]))
        note_cells.append(_p_field_small(view.payment_instructions, styles))
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
            styles["small_ui"],
        )
    )
    return out


def render_invoice_pdf(invoice) -> bytes:
    """
    Render invoice as PDF bytes (English labels).
    """
    branding = IssuerBranding.load()
    token = _use_unicode_invoice_font.set(_needs_arabic_pdf_font(invoice, branding))
    try:
        view = InvoiceView.build(invoice, branding, status_lang="en")
        return _render_invoice_pdf_body(invoice, branding, view)
    finally:
        _use_unicode_invoice_font.reset(token)


def _render_invoice_pdf_body(invoice, branding: IssuerBranding, view: InvoiceView) -> bytes:
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
