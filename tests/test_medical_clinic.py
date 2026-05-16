"""Medical specialization, reception/doctor roles, and reception visit reminders."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status

from conftest import api_body


@pytest.fixture
def reception_user(company, db):
    from accounts.models import User

    return User.objects.create_user(
        username="reception_user",
        email="reception@test.com",
        password="testpass123",
        first_name="Rec",
        last_name="Eption",
        company=company,
        role="reception",
    )


@pytest.fixture
def doctor_user(company, db):
    from accounts.models import User

    return User.objects.create_user(
        username="doctor_user",
        email="doctor@test.com",
        password="testpass123",
        first_name="Doc",
        last_name="Tor",
        company=company,
        role="doctor",
    )


@pytest.fixture
def authenticated_reception(api_client, reception_user, subscription):
    api_client.force_authenticate(user=reception_user)
    return api_client


@pytest.fixture
def authenticated_doctor(api_client, doctor_user, subscription):
    api_client.force_authenticate(user=doctor_user)
    return api_client


@pytest.mark.django_db
def test_medical_client_visit_allowed_and_sets_visited(
    authenticated_admin, company, owner_user
):
    from crm.models import Client
    from settings.lead_status_automation import VISITED_AUTOMATION_KEY, ensure_visited_lead_status
    from settings.models import VisitType

    company.specialization = "medical"
    company.save(update_fields=["specialization"])
    ensure_visited_lead_status(company)

    vt = VisitType.objects.create(
        name="First consultation",
        description="",
        color="#3b82f6",
        company=company,
        is_active=True,
        is_default=True,
    )
    client = Client.objects.create(
        name="Patient A",
        company=company,
        priority="medium",
        type="fresh",
        created_by=owner_user,
    )

    response = authenticated_admin.post(
        "/api/v1/client-visits/",
        {
            "client": client.id,
            "visit_type": vt.id,
            "summary": "Initial intake.",
            "visit_datetime": timezone.now().isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    client.refresh_from_db()
    assert client.status is not None
    assert client.status.automation_key == VISITED_AUTOMATION_KEY


@pytest.mark.django_db
def test_reception_can_create_client_visit(authenticated_reception, company, owner_user):
    from crm.models import Client
    from settings.models import VisitType

    company.specialization = "medical"
    company.save(update_fields=["specialization"])

    vt = VisitType.objects.create(
        name="Follow-up visit",
        description="",
        color="#8b5cf6",
        company=company,
        is_active=True,
        is_default=True,
    )
    client = Client.objects.create(
        name="Patient B",
        company=company,
        priority="medium",
        type="fresh",
        created_by=owner_user,
    )

    response = authenticated_reception.post(
        "/api/v1/client-visits/",
        {
            "client": client.id,
            "visit_type": vt.id,
            "summary": "Scheduled follow-up.",
            "visit_datetime": timezone.now().isoformat(),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = api_body(response)
    assert data["client"] == client.id


@pytest.mark.django_db
def test_doctor_sees_only_assigned_clients(authenticated_doctor, company, owner_user, doctor_user):
    from crm.models import Client

    company.specialization = "medical"
    company.save(update_fields=["specialization"])

    mine = Client.objects.create(
        name="Mine",
        company=company,
        priority="medium",
        type="fresh",
        assigned_to=doctor_user,
        created_by=owner_user,
    )
    Client.objects.create(
        name="Other",
        company=company,
        priority="medium",
        type="fresh",
        created_by=owner_user,
    )

    response = authenticated_doctor.get("/api/v1/clients/")
    assert response.status_code == status.HTTP_200_OK
    body = api_body(response)
    assert body["count"] == 1
    ids = {row["id"] for row in body["results"]}
    assert ids == {mine.id}


@pytest.mark.django_db
def test_check_call_reminders_notifies_reception_for_medical_visit(
    company, owner_user, reception_user, doctor_user
):
    from crm.models import Client, ClientVisit
    from settings.models import VisitType

    company.specialization = "medical"
    company.save(update_fields=["specialization"])

    vt = VisitType.objects.create(
        name="Follow-up visit",
        description="",
        color="#8b5cf6",
        company=company,
        is_active=True,
        is_default=True,
    )
    client = Client.objects.create(
        name="Reminder Patient",
        company=company,
        priority="medium",
        type="fresh",
        assigned_to=doctor_user,
        created_by=owner_user,
    )
    soon = timezone.now() + timedelta(minutes=16)
    _ = ClientVisit.objects.create(
        client=client,
        visit_type=vt,
        summary="Booked",
        visit_datetime=timezone.now(),
        upcoming_visit_date=soon,
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

    from notifications.models import NotificationType

    reception_hits = [
        kw
        for kw in sent
        if kw.get("user") and kw["user"].id == reception_user.id
        and kw.get("notification_type") == NotificationType.RECEPTION_VISIT_REMINDER
    ]
    assert reception_hits, sent
