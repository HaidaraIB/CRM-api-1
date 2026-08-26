"""Statuses flagged `requires_change_reason` demand a written reason on lead update."""
import pytest
from rest_framework import status

from conftest import api_body


@pytest.fixture
def statuses(company):
    from settings.models import LeadStatus

    plain = LeadStatus.objects.create(
        name="Plain",
        company=company,
        category="active",
        color="#111111",
    )
    gated = LeadStatus.objects.create(
        name="Not Interested",
        company=company,
        category="closed",
        color="#222222",
        requires_change_reason=True,
    )
    return plain, gated


@pytest.fixture
def lead(company, statuses):
    from crm.models import Client

    plain, _ = statuses
    return Client.objects.create(
        name="Lead",
        company=company,
        priority="medium",
        type="fresh",
        status=plain,
    )


@pytest.mark.django_db
def test_status_change_without_flag_needs_no_reason(authenticated_admin, company, lead):
    from crm.models import ClientEvent
    from settings.models import LeadStatus

    other = LeadStatus.objects.create(
        name="Other",
        company=company,
        category="active",
        color="#333333",
    )
    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"status": other.id},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    lead.refresh_from_db()
    assert lead.status_id == other.id
    event = ClientEvent.objects.get(client=lead, event_type="status_change")
    assert event.reason is None


@pytest.mark.django_db
def test_status_change_to_gated_status_without_reason_is_rejected(
    authenticated_admin, lead, statuses
):
    from crm.models import ClientEvent

    _, gated = statuses
    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"status": gated.id},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "status_change_reason" in str(response.data)
    lead.refresh_from_db()
    assert lead.status_id != gated.id
    assert not ClientEvent.objects.filter(client=lead, event_type="status_change").exists()


@pytest.mark.django_db
def test_status_change_to_gated_status_with_blank_reason_is_rejected(
    authenticated_admin, lead, statuses
):
    _, gated = statuses
    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"status": gated.id, "status_change_reason": "   "},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    lead.refresh_from_db()
    assert lead.status_id != gated.id


@pytest.mark.django_db
def test_status_change_with_reason_is_stored_on_the_event(
    authenticated_admin, lead, statuses
):
    from crm.models import ClientEvent

    _, gated = statuses
    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"status": gated.id, "status_change_reason": "Price too high"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    lead.refresh_from_db()
    assert lead.status_id == gated.id

    event = ClientEvent.objects.get(client=lead, event_type="status_change")
    assert event.reason == "Price too high"
    assert event.new_value == gated.name


@pytest.mark.django_db
def test_reason_is_exposed_by_the_client_events_endpoint(
    authenticated_admin, lead, statuses
):
    _, gated = statuses
    authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"status": gated.id, "status_change_reason": "Chose a competitor"},
        format="json",
    )
    response = authenticated_admin.get(f"/api/v1/client-events/?client={lead.id}")
    assert response.status_code == status.HTTP_200_OK
    events = api_body(response)["results"]
    change = next(e for e in events if e["event_type"] == "status_change")
    assert change["reason"] == "Chose a competitor"


@pytest.mark.django_db
def test_creating_a_lead_in_a_gated_status_needs_no_reason(
    authenticated_admin, company, statuses
):
    _, gated = statuses
    response = authenticated_admin.post(
        "/api/v1/clients/",
        {
            "name": "Fresh lead",
            "company": company.id,
            "priority": "low",
            "type": "cold",
            "status": gated.id,
            "phone_number": "+9647700000001",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_no_op_status_write_needs_no_reason(authenticated_admin, company, statuses):
    """PATCHing the same status back is not a change, so nothing to justify."""
    from crm.models import Client

    _, gated = statuses
    lead = Client.objects.create(
        name="Already there",
        company=company,
        priority="low",
        type="cold",
        status=gated,
    )
    response = authenticated_admin.patch(
        f"/api/v1/clients/{lead.id}/",
        {"status": gated.id},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_visit_automation_bypasses_the_reason_requirement(company, statuses):
    """The visit -> Visited automation writes the FK directly and must not be blocked."""
    from crm.models import Client, ClientVisit
    from settings.lead_status_automation import ensure_visited_lead_status

    company.specialization = "real_estate"
    company.save(update_fields=["specialization"])

    visited = ensure_visited_lead_status(company)
    visited.requires_change_reason = True
    visited.save(update_fields=["requires_change_reason"])

    plain, _ = statuses
    lead = Client.objects.create(
        name="Visitor",
        company=company,
        priority="low",
        type="cold",
        status=plain,
    )
    ClientVisit.objects.create(client=lead)

    lead.refresh_from_db()
    assert lead.status_id == visited.id
