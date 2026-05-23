"""Client field visit API: proximity validation and products specialization access."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status

from conftest import api_body
from crm.geo import (
    FIELD_VISIT_MAX_DISTANCE_METERS,
    haversine_distance_meters,
    field_visit_max_allowed_distance_meters,
)


@pytest.mark.django_db
def test_client_location_pair_validation(authenticated_admin, company):
    from crm.models import Client

    client = Client.objects.create(
        name="Loc Lead",
        company=company,
        priority="medium",
        type="fresh",
    )

    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/",
        {"location_latitude": "33.315200"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_field_visit_without_lead_location_products_company(authenticated_admin, company):
    from crm.models import Client

    company.specialization = "products"
    company.save(update_fields=["specialization"])

    client = Client.objects.create(
        name="Product Lead",
        company=company,
        priority="medium",
        type="fresh",
    )

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "On-site check",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": "33.315200",
            "employee_longitude": "44.366100",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = api_body(response)
    assert data["client"] == client.id
    assert data["summary"] == "On-site check"


@pytest.mark.django_db
def test_field_visit_rejected_when_too_far(authenticated_admin, company):
    from crm.models import Client

    lead_lat = Decimal("33.315200")
    lead_lng = Decimal("44.366100")
    client = Client.objects.create(
        name="Geo Lead",
        company=company,
        priority="medium",
        type="fresh",
        location_latitude=lead_lat,
        location_longitude=lead_lng,
    )

    # ~50m north of lead
    far_lat = float(lead_lat) + 0.00045
    far_lng = float(lead_lng)
    assert haversine_distance_meters(lead_lat, lead_lng, far_lat, far_lng) > 10

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "Too far",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": str(far_lat),
            "employee_longitude": str(far_lng),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    details = body.get("error", {}).get("details") or body
    errors = details.get("non_field_errors") if isinstance(details, dict) else None
    if errors is None and isinstance(body.get("error"), dict):
        errors = body["error"].get("details", {}).get("non_field_errors")
    assert errors is not None
    assert "field_visit_too_far" in errors


@pytest.mark.django_db
def test_field_visit_succeeds_within_10m(authenticated_admin, company):
    from crm.models import Client

    lead_lat = Decimal("33.315200")
    lead_lng = Decimal("44.366100")
    client = Client.objects.create(
        name="Near Lead",
        company=company,
        priority="medium",
        type="fresh",
        location_latitude=lead_lat,
        location_longitude=lead_lng,
    )

    near_lat = float(lead_lat) + 0.00001
    near_lng = float(lead_lng)
    assert haversine_distance_meters(lead_lat, lead_lng, near_lat, near_lng) <= 10

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "At location",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": str(near_lat),
            "employee_longitude": str(near_lng),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_field_visit_accepts_high_precision_browser_gps(authenticated_admin, company):
    """Browser geolocation often sends many decimal places; server quantizes to 6."""
    from crm.models import Client

    client = Client.objects.create(
        name="No lead location",
        company=company,
        priority="medium",
        type="fresh",
    )

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "GPS from browser",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": 33.315200123456789,
            "employee_longitude": 44.366100987654321,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = api_body(response)
    assert str(data["employee_latitude"]).startswith("33.3152")
    assert str(data["employee_longitude"]).startswith("44.3661")


@pytest.mark.django_db
def test_field_visit_allows_separate_gps_fixes_with_accuracy_buffer(
    authenticated_admin, company,
):
    """Two 'current location' reads can be >10 m apart; accuracy expands allowed range."""
    from crm.models import Client

    lead_lat = Decimal("33.315200")
    lead_lng = Decimal("44.366100")
    client = Client.objects.create(
        name="GPS drift lead",
        company=company,
        priority="medium",
        type="fresh",
        location_latitude=lead_lat,
        location_longitude=lead_lng,
    )
    # ~18 m north — fails strict 10 m, passes with 10 + 15 accuracy buffer
    emp_lat = float(lead_lat) + 0.00016
    emp_lng = float(lead_lng)
    dist = haversine_distance_meters(lead_lat, lead_lng, emp_lat, emp_lng)
    assert dist > 10
    assert dist < field_visit_max_allowed_distance_meters(15.0)

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "Near enough with GPS buffer",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": emp_lat,
            "employee_longitude": emp_lng,
            "employee_location_accuracy": 15,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_field_visit_requires_employee_coordinates(authenticated_admin, company):
    from crm.models import Client

    client = Client.objects.create(
        name="No GPS Lead",
        company=company,
        priority="medium",
        type="fresh",
    )

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "Missing coords",
            "visit_datetime": timezone.now().isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_check_call_reminders_notifies_assignee_for_field_visit(
    company, owner_user, employee_user
):
    from crm.models import Client, ClientFieldVisit
    from notifications.models import NotificationType

    client = Client.objects.create(
        name="Field Visit Lead",
        company=company,
        priority="medium",
        type="fresh",
        assigned_to=employee_user,
        created_by=owner_user,
    )
    soon = timezone.now() + timedelta(minutes=16)
    ClientFieldVisit.objects.create(
        client=client,
        summary="Scheduled follow-up",
        visit_datetime=timezone.now(),
        upcoming_visit_date=soon,
        employee_latitude="33.315200",
        employee_longitude="44.366100",
        created_by=owner_user,
    )

    sent = []

    def capture_send(*args, **kwargs):
        sent.append(kwargs)

    with patch(
        "notifications.management.commands.check_call_reminders.NotificationService.send_notification",
        side_effect=capture_send,
    ), patch(
        "notifications.management.commands.check_call_reminders.send_followup_reminder_email",
        return_value=True,
    ):
        call_command(
            "check_call_reminders",
            minutes_before=15,
            window_minutes=30,
        )

    assignee_hits = [
        kw
        for kw in sent
        if kw.get("user")
        and kw["user"].id == employee_user.id
        and kw.get("notification_type") == NotificationType.FIELD_VISIT_REMINDER
    ]
    assert assignee_hits, sent
    assert assignee_hits[0]["data"].get("field_visit_id") is not None
