"""Client visit API: specialization gate and Visited lead status automation."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from conftest import api_body


@pytest.mark.django_db
def test_client_visit_rejected_for_products_specialization(authenticated_admin, company):
    from crm.models import Client
    from settings.models import VisitType

    company.specialization = "products"
    company.save(update_fields=["specialization"])

    vt = VisitType.objects.create(
        name="Site",
        description="",
        color="#111111",
        company=company,
        is_active=True,
        is_default=True,
    )
    client = Client.objects.create(
        name="Product Lead",
        company=company,
        priority="medium",
        type="fresh",
    )

    response = authenticated_admin.post(
        "/api/v1/client-visits/",
        {
            "client": client.id,
            "visit_type": vt.id,
            "summary": "Walk-in",
            "visit_datetime": timezone.now().isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_client_visit_creates_and_sets_visited_status(authenticated_admin, company):
    from crm.models import Client
    from settings.lead_status_automation import VISITED_AUTOMATION_KEY, ensure_visited_lead_status
    from settings.models import VisitType

    ensure_visited_lead_status(company)
    vt = VisitType.objects.create(
        name="Site tour",
        description="",
        color="#6366f1",
        company=company,
        is_active=True,
        is_default=True,
    )
    client = Client.objects.create(
        name="RE Lead",
        company=company,
        priority="high",
        type="fresh",
    )

    upcoming = (timezone.now() + timedelta(days=3)).replace(microsecond=0)

    response = authenticated_admin.post(
        "/api/v1/client-visits/",
        {
            "client": client.id,
            "visit_type": vt.id,
            "summary": "Client viewed the unit.",
            "visit_datetime": timezone.now().isoformat(),
            "upcoming_visit_date": upcoming.isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = api_body(response)
    assert data["client"] == client.id
    assert data["visit_type"] == vt.id
    assert data["summary"] == "Client viewed the unit."
    assert data.get("upcoming_visit_date")

    client.refresh_from_db()
    assert client.status is not None
    assert client.status.automation_key == VISITED_AUTOMATION_KEY
    assert client.status_entered_at is not None
    assert (timezone.now() - client.status_entered_at).total_seconds() < 120
