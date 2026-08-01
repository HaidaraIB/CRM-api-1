"""Regression: lead updates must not log no-op field edit events."""
import pytest

from crm.models import Client, ClientEvent
from crm.serializers import _client_field_values_equal


@pytest.mark.django_db
def test_client_field_values_equal_empty_string_and_none():
    assert _client_field_values_equal(None, "") is True
    assert _client_field_values_equal("", None) is True
    assert _client_field_values_equal("", "") is True
    assert _client_field_values_equal(None, None) is True
    assert _client_field_values_equal("a", None) is False
    assert _client_field_values_equal("a", "b") is False


@pytest.mark.django_db
def test_put_only_name_change_skips_empty_optional_noops(
    authenticated_employee,
    company,
    employee_user,
):
    """
    DB may store '' for blank optionals while the web client sends null.
    Changing only name must not create field_updated events for those fields.
    """
    lead = Client.objects.create(
        name="Original Name",
        company=company,
        priority="medium",
        type="fresh",
        assigned_to=employee_user,
        notes="",
        profession="",
        residence="",
        lead_company_name="",
        budget=None,
        budget_max=None,
    )

    response = authenticated_employee.put(
        f"/api/v1/clients/{lead.id}/",
        {
            "name": "New Name",
            "company": company.id,
            "priority": "medium",
            "type": "fresh",
            "assigned_to": employee_user.id,
            "notes": None,
            "profession": None,
            "residence": None,
            "lead_company_name": None,
            "budget": None,
            "budget_max": None,
        },
        format="json",
    )
    assert response.status_code == 200, getattr(response, "data", response.content)

    edit_events = list(
        ClientEvent.objects.filter(client=lead, event_type="edit").order_by("id")
    )
    assert len(edit_events) == 1
    assert edit_events[0].notes == "field_updated:name"
    assert edit_events[0].old_value == "Original Name"
    assert edit_events[0].new_value == "New Name"


@pytest.mark.django_db
def test_real_notes_change_still_logged(
    authenticated_employee,
    company,
    employee_user,
):
    lead = Client.objects.create(
        name="Lead",
        company=company,
        priority="medium",
        type="fresh",
        assigned_to=employee_user,
        notes="old note",
    )

    response = authenticated_employee.patch(
        f"/api/v1/clients/{lead.id}/",
        {"notes": "new note"},
        format="json",
    )
    assert response.status_code == 200, getattr(response, "data", response.content)

    edit_events = list(
        ClientEvent.objects.filter(client=lead, event_type="edit").order_by("id")
    )
    assert len(edit_events) == 1
    assert edit_events[0].notes == "field_updated:notes"
    assert edit_events[0].old_value == "old note"
    assert edit_events[0].new_value == "new note"
