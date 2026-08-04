"""Tests for shared message placeholders (curly + bracket forms)."""

from integrations.services.message_placeholders import (
    build_message_placeholder_values,
    render_message_placeholders,
)
from integrations.views.templates_whatsapp import _content_to_meta_body, _find_placeholders_in_order


class FakeCompany:
    name = "TenantCo"
    timezone = "Asia/Baghdad"


class FakeStatus:
    name = "جديد"


class FakeChannel:
    name = "واتساب"


class FakeUser:
    first_name = "Ali"
    last_name = "Hassan"
    username = "ali"

    def get_full_name(self):
        return "Ali Hassan"


class FakeClient:
    name = "Sara Ahmed"
    phone_number = "+9647701112233"
    profession = "مهندس"
    lead_company_name = "LeadCo"
    budget = None
    budget_max = None
    invoice_number = None
    priority = "high"
    type = "fresh"
    source = "meta"
    company = FakeCompany()
    status = FakeStatus()
    communication_way = FakeChannel()
    assigned_to = FakeUser()
    pk = 1

    class _EmptyQS:
        def select_related(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def first(self):
            return None

    client_tasks = _EmptyQS()
    client_visits = _EmptyQS()


def test_render_arabic_curly_placeholders():
    client = FakeClient()
    values = build_message_placeholder_values(client)
    text = (
        "مرحبا { اسم العميل } هاتف { رقم الهاتف } موظف { اسم الموظف } "
        "شركة { اسم الشركة } حالة { الحالة } مهنة { المهنة } قناة { قناة التواصل }"
    )
    out = render_message_placeholders(text, values)
    assert "Sara Ahmed" in out
    assert "+9647701112233" in out
    assert "Ali Hassan" in out
    assert "TenantCo" in out
    assert "جديد" in out
    assert "مهندس" in out
    assert "واتساب" in out
    assert "{" not in out


def test_render_keeps_meta_positional():
    values = {"customer_name": "Sara"}
    out = render_message_placeholders("Hi {{1}} and { اسم العميل }", values)
    assert "{{1}}" in out
    assert "Sara" in out


def test_meta_conversion_finds_curly_tokens():
    content = "Hello { اسم العميل } from { اسم الشركة }"
    matches = _find_placeholders_in_order(content)
    assert len(matches) == 2
    body, samples = _content_to_meta_body(content)
    assert "{{1}}" in body
    assert "{{2}}" in body
    assert len(samples) == 2


def test_legacy_bracket_still_works():
    values = build_message_placeholder_values(FakeClient())
    out = render_message_placeholders("Hi [Customer Name] / [name]", values)
    assert out == "Hi Sara Ahmed / Sara Ahmed"
