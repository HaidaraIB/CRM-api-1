"""
Send invoice email (HTML summary + PDF attachment).
"""
from __future__ import annotations

import logging

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from subscriptions.invoicing.branding import IssuerBranding
from subscriptions.invoicing.pdf import render_invoice_pdf_bytes
from subscriptions.invoicing.view_model import InvoiceView, format_payment_status_label

logger = logging.getLogger(__name__)


def _normalize_invoice_email_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    code = str(lang).strip().lower()[:2]
    return code if code in ("ar", "en") else None


def _resolve_invoice_email_language(company, recipient: str) -> str:
    r = (recipient or "").strip().lower()
    if not r:
        return "ar"
    if company is not None:
        owner = getattr(company, "owner", None)
        if owner and (owner.email or "").strip().lower() == r:
            raw = getattr(owner, "language", None)
            ol = str(raw).strip().lower()[:2] if raw is not None else ""
            if ol == "en":
                return "en"
            return "ar"
        from accounts.models import User

        user = (
            User.objects.filter(company_id=company.pk, email__iexact=r)
            .only("language")
            .first()
        )
        if user:
            raw = getattr(user, "language", None)
            ul = str(raw).strip().lower()[:2] if raw is not None else ""
            if ul == "en":
                return "en"
            return "ar"
    return "ar"


def _invoice_email_subject(invoice, company_name: str, lang: str) -> str:
    cn = (company_name or "").strip()
    num = invoice.invoice_number
    if lang == "en":
        base = f"Invoice {num}"
        return f"{base} — {cn}" if cn else base
    base = f"فاتورة {num}"
    return f"{base} — {cn}" if cn else base


def _invoice_plain_body(lang: str) -> str:
    if lang == "en":
        return (
            "Your invoice is attached as a PDF. "
            "If this message does not display correctly, open the attachment."
        )
    return (
        "مرفق مع هذه الرسالة ملف PDF للفاتورة. "
        "إذا لم يُعرض المحتوى بشكل صحيح، يُرجى فتح المرفق."
    )


def _invoice_email_context(
    *,
    invoice,
    invoice_view: InvoiceView,
    company_name: str,
    from_name: str,
    payment_status_label: str,
    lang: str,
) -> dict:
    cn = company_name or "—"
    view_dict = invoice_view.as_template_dict()
    if lang == "en":
        return {
            "invoice": invoice,
            "invoice_view": view_dict,
            "company_name": cn,
            "from_name": from_name,
            "payment_status_label": payment_status_label,
            "label_invoice": "Invoice",
            "label_for": "For",
            "greeting": "Hello,",
            "intro_prefix": "Thank you for your business. Below is a summary of invoice ",
            "intro_suffix": ". The full document is attached as a PDF.",
            "label_company": "Billed to",
            "label_description": "Description",
            "label_status": "Payment status",
            "label_due_date": "Due date",
            "label_issue_date": "Issue date",
            "label_total": "Amount due",
            "label_subtotal": "Subtotal",
            "attachment_title": "PDF attached",
            "attachment_prefix": "The file ",
            "attachment_suffix": " is attached to this email. Keep it for your records.",
            "closing": "If you have questions about this invoice, reply to this email or contact support.",
            "footer_rights": "All rights reserved.",
        }
    return {
        "invoice": invoice,
        "invoice_view": view_dict,
        "company_name": cn,
        "from_name": from_name,
        "payment_status_label": payment_status_label,
        "label_invoice": "فاتورة",
        "label_for": "الجهة",
        "greeting": "مرحباً،",
        "intro_prefix": "شكراً لتعاملكم معنا. فيما يلي ملخص الفاتورة ",
        "intro_suffix": ". النسخة الكاملة مرفقة بصيغة PDF.",
        "label_company": "العميل",
        "label_description": "الوصف",
        "label_status": "حالة الدفع",
        "label_due_date": "تاريخ الاستحقاق",
        "label_issue_date": "تاريخ الإصدار",
        "label_total": "المبلغ",
        "label_subtotal": "المجموع الفرعي",
        "attachment_title": "مرفق PDF",
        "attachment_prefix": "الملف ",
        "attachment_suffix": " مرفق بهذه الرسالة. يُرجى الاحتفاظ به لسجلاتكم.",
        "closing": "لأي استفسار بخصوص هذه الفاتورة، يمكنكم الرد على هذا البريد أو التواصل مع الدعم.",
        "footer_rights": "جميع الحقوق محفوظة.",
    }


def send_invoice_email(
    invoice,
    to_email: str | None = None,
    language: str | None = None,
) -> tuple[bool, str]:
    """
    Email the invoice PDF to the tenant company owner (or to_email override).
    HTML language is ``ar`` / ``en``; PDF attachment is English-only.
    Returns (success, message).
    """
    from crm_saas_api.email_exceptions import OutboundEmailNotConfiguredError
    from crm_saas_api.utils import (
        format_platform_from_address,
        get_platform_email_display_name,
        get_smtp_connection,
    )
    from settings.models import SMTPSettings

    subscription = invoice.subscription
    company = subscription.company if subscription else None
    recipient = (to_email or "").strip()
    if not recipient and company:
        owner = getattr(company, "owner", None)
        if owner and getattr(owner, "email", None):
            recipient = owner.email.strip()
    if not recipient:
        return False, "No recipient email (company owner email missing)."

    lang = _normalize_invoice_email_lang(language) or _resolve_invoice_email_language(
        company, recipient
    )
    template_name = f"subscriptions/invoice_email_{lang}.html"

    smtp_settings = SMTPSettings.get_settings()
    if not smtp_settings.is_active:
        return (
            False,
            "Outbound email is disabled. Enable it in platform email settings and set RESEND_API_KEY.",
        )

    try:
        connection = get_smtp_connection()
    except OutboundEmailNotConfiguredError as e:
        return False, str(e)

    branding = IssuerBranding.load()
    invoice_view = InvoiceView.build(invoice, branding, status_lang=lang)
    pdf_bytes = render_invoice_pdf_bytes(invoice, language=lang)
    company_name = invoice.company_name or (company.name if company else "")
    status_code = invoice.effective_payment_status()
    payment_status_label = format_payment_status_label(status_code, lang)
    from_name = get_platform_email_display_name(smtp_settings)
    ctx = _invoice_email_context(
        invoice=invoice,
        invoice_view=invoice_view,
        company_name=company_name,
        from_name=from_name,
        payment_status_label=payment_status_label,
        lang=lang,
    )
    subject = _invoice_email_subject(invoice, company_name, lang)
    body_html = render_to_string(template_name, ctx)
    from_email = format_platform_from_address(smtp_settings)
    mail = EmailMultiAlternatives(
        subject=subject,
        body=_invoice_plain_body(lang),
        from_email=from_email,
        to=[recipient],
        connection=connection,
    )
    mail.attach_alternative(body_html, "text/html")
    mail.attach(
        f"{invoice.invoice_number}.pdf",
        pdf_bytes,
        "application/pdf",
    )
    try:
        mail.send(fail_silently=False)
    except Exception as e:
        logger.exception("send_invoice_email failed: %s", e)
        return False, str(e)

    invoice.last_emailed_at = timezone.now()
    invoice.save(update_fields=["last_emailed_at", "updated_at"])
    return True, "Sent"
