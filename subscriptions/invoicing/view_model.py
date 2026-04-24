"""
Renderer-agnostic invoice view (PDF + email) built from Invoice + IssuerBranding.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from django.utils import timezone

from subscriptions.invoicing.branding import IssuerBranding


def _format_amount(amount: Any) -> str:
    try:
        d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except Exception:
        return str(amount)
    return format(d.quantize(Decimal("0.01")), "f")


def _money_display(currency: str, amount_str: str) -> str:
    cur = (currency or "USD").strip().upper()
    if cur == "USD":
        return f"${amount_str}"
    return f"{amount_str} {cur}"


def format_payment_status_label(code: str, lang: str) -> str:
    c = (code or "").strip().lower()
    en = {
        "pending": "Pending",
        "completed": "Paid",
        "failed": "Failed",
        "canceled": "Canceled",
    }
    ar = {
        "pending": "قيد الانتظار",
        "completed": "مدفوع",
        "failed": "فشلت",
        "canceled": "ملغاة",
    }
    labels = en if lang == "en" else ar
    return labels.get(c, code.replace("_", " ").title() if lang == "en" else code)


def _status_style(code: str) -> tuple[str, str]:
    """(background_hex, text_hex) for status pill."""
    c = (code or "").strip().lower()
    styles = {
        "completed": ("#ecfdf5", "#047857"),
        "pending": ("#ede9fe", "#5b21b6"),
        "failed": ("#fef2f2", "#b91c1c"),
        "canceled": ("#f9fafb", "#4b5563"),
    }
    return styles.get(c, styles["pending"])


def _line_item_description(invoice) -> str:
    ld = (getattr(invoice, "line_description", None) or "").strip()
    if ld:
        return ld
    pn = (getattr(invoice, "plan_name", None) or "").strip()
    bc = (getattr(invoice, "billing_cycle", None) or "").strip()
    if pn and bc:
        return f"{pn} — {bc}"
    if pn:
        return pn
    return "Subscription"


def _issuer_address_lines(branding: IssuerBranding) -> list[str]:
    raw = (branding.issuer_address or "").strip()
    if not raw:
        return []
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


@dataclass(frozen=True)
class InvoiceView:
    invoice_number: str
    issue_date_str: str
    due_date_str: str
    company_name: str
    line_item_description: str
    plan_name: str
    billing_cycle: str
    currency: str
    amount_numeric_str: str
    money_display: str
    subtotal_display: str
    total_display: str
    status_code: str
    status_label: str
    status_bg: str
    status_fg: str
    issuer_name: str
    issuer_address_lines: tuple[str, ...]
    issuer_email: str
    issuer_phone: str
    issuer_tax_id: str
    footer_text: str
    payment_instructions: str
    platform_name: str
    has_logo: bool

    def as_template_dict(self) -> dict[str, Any]:
        """Flat dict for Django templates (no bytes)."""
        d = asdict(self)
        d["issuer_address_lines"] = list(self.issuer_address_lines)
        return d

    @classmethod
    def build(cls, invoice, branding: IssuerBranding, *, status_lang: str = "en") -> InvoiceView:
        status_code = invoice.effective_payment_status()
        status_label = format_payment_status_label(status_code, status_lang)
        status_bg, status_fg = _status_style(status_code)

        created = getattr(invoice, "created_at", None)
        if created:
            issue_date_str = timezone.localtime(created).strftime("%b %d, %Y")
        else:
            issue_date_str = ""

        due = getattr(invoice, "due_date", None)
        due_date_str = due.strftime("%b %d, %Y") if due else ""

        cur = (getattr(invoice, "currency", None) or "USD").strip().upper()[:10]
        amount_str = _format_amount(getattr(invoice, "amount", 0))
        money = _money_display(cur, amount_str)

        addr_lines = tuple(_issuer_address_lines(branding))
        line_desc = _line_item_description(invoice)

        return cls(
            invoice_number=getattr(invoice, "invoice_number", "") or "",
            issue_date_str=issue_date_str,
            due_date_str=due_date_str,
            company_name=(getattr(invoice, "company_name", None) or "").strip() or "—",
            line_item_description=line_desc,
            plan_name=(getattr(invoice, "plan_name", None) or "").strip(),
            billing_cycle=(getattr(invoice, "billing_cycle", None) or "").strip(),
            currency=cur,
            amount_numeric_str=amount_str,
            money_display=money,
            subtotal_display=money,
            total_display=money,
            status_code=status_code or "",
            status_label=status_label,
            status_bg=status_bg,
            status_fg=status_fg,
            issuer_name=(branding.issuer_name or "").strip() or branding.platform_name,
            issuer_address_lines=addr_lines,
            issuer_email=(branding.issuer_email or "").strip(),
            issuer_phone=(branding.issuer_phone or "").strip(),
            issuer_tax_id=(branding.issuer_tax_id or "").strip(),
            footer_text=(branding.footer_text or "").strip(),
            payment_instructions=(branding.payment_instructions or "").strip(),
            platform_name=branding.platform_name,
            has_logo=bool(branding.logo_bytes),
        )
