"""ReportLab invoice PDF and InvoiceView."""
from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from django.utils import timezone
from pypdf import PdfReader

from subscriptions.invoicing import render_invoice_pdf_bytes
from subscriptions.invoicing.branding import IssuerBranding
from subscriptions.invoicing.pdf import render_invoice_pdf
from subscriptions.invoicing.view_model import InvoiceView
from subscriptions.views.viewsets_public import InvoiceViewSet


def _minimal_invoice():
    return SimpleNamespace(
        invoice_number="INV-2026-00001",
        amount=Decimal("10.50"),
        currency="USD",
        company_name="Acme LLC",
        plan_name="Pro",
        line_description="Pro — monthly",
        billing_cycle="monthly",
        due_date=date(2026, 6, 1),
        created_at=timezone.now(),
        payment_id=1,
        payment=SimpleNamespace(payment_status="completed"),
        subscription=None,
        effective_payment_status=lambda: "completed",
    )


@pytest.mark.django_db
def test_render_invoice_pdf_generates_valid_pdf(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    (tmp_path / "media").mkdir(parents=True)

    pdf = render_invoice_pdf(_minimal_invoice())
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500


@pytest.mark.django_db
def test_render_invoice_pdf_text_contains_expected_fields(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    (tmp_path / "media").mkdir(parents=True)

    pdf = render_invoice_pdf(_minimal_invoice())
    reader = PdfReader(BytesIO(pdf))
    text = "".join((page.extract_text() or "") for page in reader.pages)

    assert "INV-2026-00001" in text
    assert "Acme LLC" in text
    assert "Pro — monthly" in text or "Pro" in text
    assert "10.50" in text
    assert "INVOICE" in text
    assert "FROM" in text
    assert "BILL TO" in text
    assert "Subtotal" in text
    assert "Total" in text
    assert "Paid" in text or "Payment status" in text


def test_invoice_view_formats_money_and_status():
    inv = _minimal_invoice()
    branding = IssuerBranding(
        issuer_name="Issuer Inc",
        issuer_address="123 Main St\nSuite 1",
        issuer_email="billing@issuer.test",
        issuer_phone="+1 555",
        issuer_tax_id="TAX-1",
        footer_text="Thanks.",
        payment_instructions="Wire us.",
        logo_bytes=None,
        logo_mime=None,
        platform_name="LOOP CRM",
    )
    view = InvoiceView.build(inv, branding, status_lang="en")
    assert view.total_display == "$10.50"
    assert view.status_label == "Paid"
    assert view.line_item_description == "Pro — monthly"

    view_ar = InvoiceView.build(inv, branding, status_lang="ar")
    assert view_ar.status_label == "مدفوع"


def test_invoice_view_fallbacks():
    inv = SimpleNamespace(
        invoice_number="INV-X",
        amount=Decimal("1"),
        currency="IQD",
        company_name="",
        plan_name="",
        line_description="",
        billing_cycle="",
        due_date=None,
        created_at=None,
        effective_payment_status=lambda: "pending",
    )
    b = IssuerBranding("", "", "", "", "", "", "", None, None, "PN")
    view = InvoiceView.build(inv, b, status_lang="en")
    assert view.company_name == "—"
    assert "IQD" in view.total_display or "1.00" in view.total_display
    assert view.issue_date_str == ""


def test_invoice_pdf_action_uses_invoicing_module(monkeypatch):
    invoice = SimpleNamespace(invoice_number="INV-2026-00001")
    captured = {}

    def _fake(obj, language=None):
        captured["invoice"] = obj
        captured["language"] = language
        return b"%PDF-1.4 fake"

    monkeypatch.setattr("subscriptions.invoicing.render_invoice_pdf_bytes", _fake)

    viewset = InvoiceViewSet()
    viewset.get_object = lambda: invoice
    request = SimpleNamespace(query_params={}, headers={"X-Language": "ar"}, user=SimpleNamespace(language="ar"))

    response = viewset.pdf(request)

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'attachment; filename="INV-2026-00001.pdf"'
    assert response.content == b"%PDF-1.4 fake"
    assert captured["invoice"] is invoice
    assert captured["language"] is None


def test_render_invoice_pdf_bytes_ignores_language(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / "media")
    (tmp_path / "media").mkdir(parents=True)
    pdf = render_invoice_pdf_bytes(_minimal_invoice(), language="ar")
    assert pdf.startswith(b"%PDF")


def test_invoice_send_email_viewset_action(monkeypatch):
    invoice = SimpleNamespace(invoice_number="INV-2026-00001")
    captured = {}

    def _fake_send_invoice_email(obj, to_email=None, language=None):
        captured["invoice"] = obj
        captured["to_email"] = to_email
        captured["language"] = language
        return True, "Sent"

    monkeypatch.setattr("subscriptions.invoicing.send_invoice_email", _fake_send_invoice_email)

    viewset = InvoiceViewSet()
    viewset.get_object = lambda: invoice
    request = SimpleNamespace(data={"to": "owner@example.com", "language": "en"})

    response = viewset.send_email(request)

    assert response.status_code == 200
    assert captured["invoice"] is invoice
    assert captured["to_email"] == "owner@example.com"
    assert captured["language"] is None
