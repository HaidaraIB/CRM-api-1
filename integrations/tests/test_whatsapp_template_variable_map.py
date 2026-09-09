"""Meta {{n}} → CRM variable mapping stays fixed after submission.

Meta freezes the numbering of an approved template. The CRM body can drift away from it
(sync rewrites it to positional {{n}}, an edit can reorder the named tokens), so send-time
parameters must follow the stored map, not a re-read of `content`.
"""

from types import SimpleNamespace

from integrations.views.templates_whatsapp import (
    _content_to_meta_body,
    _variable_map_from_meta_components,
    build_template_variable_map,
    content_placeholder_canonicals,
    count_template_placeholders,
    leftover_crm_placeholders_after_meta_convert,
    template_body_parameter_values,
    template_outbound_log_body,
    values_for_canonicals,
)


class FakeCompany:
    name = "TenantCo"
    timezone = "Asia/Baghdad"


class FakeUser:
    first_name = "Ali"
    last_name = "Hassan"
    username = "ali"

    def get_full_name(self):
        return "Ali Hassan"


class _EmptyQS:
    def select_related(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return None


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
    status = None
    communication_way = None
    assigned_to = FakeUser()
    pk = 1
    client_tasks = _EmptyQS()
    client_visits = _EmptyQS()


def _template(**kwargs):
    base = {
        "content": "",
        "header_text": "",
        "header_type": "none",
        "buttons": [],
        "meta_variable_map": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


EMPLOYEE_FIRST = "معك { اسم الموظف } من { اسم الشركة } مرحباً { اسم العميل }"


def test_submit_map_records_appearance_order():
    tpl = _template(content=EMPLOYEE_FIRST)
    assert build_template_variable_map(tpl) == {
        "body": ["employee_name", "company_name", "customer_name"]
    }
    body, _samples = _content_to_meta_body(EMPLOYEE_FIRST)
    assert body.index("{{1}}") < body.index("{{2}}") < body.index("{{3}}")


def test_stored_map_survives_sync_rewriting_content_to_positional():
    """The reported bug: after sync, {{1}} silently became customer instead of employee."""
    tpl = _template(
        content="معك {{1}} من {{2}} مرحباً {{3}}",
        meta_variable_map={"body": ["employee_name", "company_name", "customer_name"]},
    )
    assert template_body_parameter_values(tpl, FakeClient()) == [
        "Ali Hassan",
        "TenantCo",
        "Sara Ahmed",
    ]


def test_positional_content_without_map_falls_back_to_the_old_guess():
    tpl = _template(content="معك {{1}} من {{2}} مرحباً {{3}}")
    assert template_body_parameter_values(tpl, FakeClient()) == [
        "Sara Ahmed",
        "TenantCo",
        "+9647701112233",
    ]


def test_stored_map_wins_over_reordered_content():
    """Editing the body after approval must not move values between Meta's slots."""
    tpl = _template(
        content="مرحباً { اسم العميل } معك { اسم الموظف } من { اسم الشركة }",
        meta_variable_map={"body": ["employee_name", "company_name", "customer_name"]},
    )
    assert template_body_parameter_values(tpl, FakeClient()) == [
        "Ali Hassan",
        "TenantCo",
        "Sara Ahmed",
    ]


def test_named_content_without_map_still_resolves_in_place():
    tpl = _template(content=EMPLOYEE_FIRST)
    assert template_body_parameter_values(tpl, FakeClient()) == [
        "Ali Hassan",
        "TenantCo",
        "Sara Ahmed",
    ]


def test_every_placeholder_resolves_to_its_own_field():
    canonicals = [
        "customer_name",
        "phone",
        "employee_name",
        "company_name",
        "profession",
        "lead_company_name",
    ]
    assert values_for_canonicals(canonicals, FakeClient()) == [
        "Sara Ahmed",
        "+9647701112233",
        "Ali Hassan",
        "TenantCo",
        "مهندس",
        "LeadCo",
    ]


def test_unassigned_lead_uses_the_sender_for_employee_name():
    """An inbound WhatsApp lead has no assignee — { اسم الموظف } must not render "-"."""

    class Unassigned(FakeClient):
        assigned_to = None

    assert values_for_canonicals(
        ["employee_name"], Unassigned(), sender_name="زينب نزار"
    ) == ["زينب نزار"]


def test_sender_wins_over_the_lead_assignee():
    """Shared inbox: the message is signed by whoever actually wrote it."""
    assert values_for_canonicals(
        ["employee_name"], FakeClient(), sender_name="زينب نزار"
    ) == ["زينب نزار"]


def test_assignee_used_when_there_is_no_sender():
    assert values_for_canonicals(["employee_name"], FakeClient()) == ["Ali Hassan"]


def test_employee_name_without_assignee_or_fallback_is_dash():
    class Unassigned(FakeClient):
        assigned_to = None

    assert values_for_canonicals(["employee_name"], Unassigned()) == ["-"]


def test_empty_value_becomes_dash_not_a_neighbouring_field():
    class NoProfession(FakeClient):
        profession = ""

    assert values_for_canonicals(["profession", "customer_name"], NoProfession()) == [
        "-",
        "Sara Ahmed",
    ]


def test_map_recovered_from_meta_example_row():
    components = [
        {
            "type": "BODY",
            "text": "معك {{1}} من {{2}} مرحباً {{3}}",
            "example": {"body_text": [["Employee", "Company", "Customer"]]},
        }
    ]
    assert _variable_map_from_meta_components(components) == {
        "body": ["employee_name", "company_name", "customer_name"]
    }


def test_unrecognized_example_values_recover_nothing():
    components = [
        {
            "type": "BODY",
            "text": "Hi {{1}}",
            "example": {"body_text": [["أحمد"]]},
        }
    ]
    assert _variable_map_from_meta_components(components) == {}


def test_example_row_shorter_than_variables_is_rejected():
    components = [
        {
            "type": "BODY",
            "text": "Hi {{1}} and {{2}}",
            "example": {"body_text": [["Customer"]]},
        }
    ]
    assert _variable_map_from_meta_components(components) == {}


def test_named_body_is_equivalent_to_its_meta_positional_form():
    """Guards the sync check that keeps named content instead of overwriting it."""
    converted, _samples = _content_to_meta_body(EMPLOYEE_FIRST)
    assert converted == "معك {{1}} من {{2}} مرحباً {{3}}"
    assert content_placeholder_canonicals(EMPLOYEE_FIRST) == [
        "employee_name",
        "company_name",
        "customer_name",
    ]


# Exact body from tenant company 26 / template id 24 (lead_follow_up).
LEAD_FOLLOW_UP = (
    "السلام عليكم معك { اسم الموظف } من { اسم الشركة } احب اتابع معك بخصوص "
    "شراء الوحدة السكنية في المجمع هل حضرتك مازلت مهتم بالشراء ؟"
)


def test_lead_follow_up_map_fills_employee_then_company():
    tpl = _template(
        content=LEAD_FOLLOW_UP,
        name="lead_follow_up",
        id=24,
        meta_variable_map={"body": ["employee_name", "company_name"]},
    )
    assert template_body_parameter_values(tpl, FakeClient(), sender_name="زينب نزار") == [
        "زينب نزار",
        "TenantCo",
    ]


def test_count_template_placeholders_prefers_map_over_empty_scan():
    """Even if content had no recognizable chips, a stored map means 2 body slots."""
    tpl = _template(
        content="static text with no chips",
        meta_variable_map={"body": ["employee_name", "company_name"]},
    )
    body_n, header_n = count_template_placeholders(tpl)
    assert body_n == 2
    assert header_n == 0


def test_outbound_log_body_fills_lead_follow_up_chips():
    tpl = _template(
        content=LEAD_FOLLOW_UP,
        name="lead_follow_up",
        id=24,
        meta_variable_map={"body": ["employee_name", "company_name"]},
    )
    params = template_body_parameter_values(tpl, FakeClient(), sender_name="زينب نزار")
    out = template_outbound_log_body(tpl, params)
    assert "{ اسم الموظف }" not in out
    assert "{ اسم الشركة }" not in out
    assert "زينب نزار" in out
    assert "TenantCo" in out


def test_bidi_mark_inside_chip_still_converts():
    # U+200F RIGHT-TO-LEFT MARK between words — common when pasting in RTL editors.
    content = "معك { اسم\u200f الموظف } من { اسم الشركة }"
    matches = content_placeholder_canonicals(content)
    assert matches == ["employee_name", "company_name"]
    converted, samples = _content_to_meta_body(content)
    assert "{{1}}" in converted and "{{2}}" in converted
    assert samples == ["Employee", "Company"]
    assert leftover_crm_placeholders_after_meta_convert(content) == []


def test_alef_variant_chip_still_resolves():
    # إسم (alef with hamza) should fold to اسم
    content = "مرحبا { إسم الموظف } من { اسم الشركة }"
    assert content_placeholder_canonicals(content) == ["employee_name", "company_name"]


def test_leftover_unknown_braces_detected_after_convert():
    content = "Hello { unknown_chip } and { اسم الموظف }"
    leftovers = leftover_crm_placeholders_after_meta_convert(content)
    assert any("unknown_chip" in t for t in leftovers)
    # Known chip should have become {{1}}
    converted, _ = _content_to_meta_body(content)
    assert "{{1}}" in converted


def test_header_chips_convert_like_body():
    header = "من { اسم الشركة }"
    converted, samples = _content_to_meta_body(header)
    assert converted == "من {{1}}"
    assert samples == ["Company"]
    assert leftover_crm_placeholders_after_meta_convert(header) == []

