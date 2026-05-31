"""
Tests for automatic inactive-client reassignment (crm.tasks.re_assign_inactive_clients).
"""
# pylint: disable=no-member

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from crm.models import Client, ClientCall, ClientEvent
from crm.tasks import re_assign_inactive_clients
from integrations.models import LeadWhatsAppMessage

User = get_user_model()


@pytest.fixture
def reassign_company(company):
    company.re_assign_enabled = True
    company.re_assign_hours = 24
    company.auto_assign_enabled = True
    company.save(update_fields=["re_assign_enabled", "re_assign_hours", "auto_assign_enabled"])
    return company


@pytest.fixture
def employee_a(reassign_company):
    return User.objects.create_user(
        username="emp_a",
        email="emp_a@test.com",
        role="employee",
        company=reassign_company,
        is_active=True,
    )


@pytest.fixture
def employee_b(reassign_company):
    return User.objects.create_user(
        username="emp_b",
        email="emp_b@test.com",
        role="employee",
        company=reassign_company,
        is_active=True,
    )


@pytest.mark.django_db
def test_does_not_reassign_when_assignee_contacted_since_assignment(
    reassign_company, employee_a, employee_b
):
    assigned_at = timezone.now() - timedelta(hours=48)
    client = Client.objects.create(
        name="Contacted Lead",
        company=reassign_company,
        assigned_to=employee_a,
        assigned_at=assigned_at,
    )
    ClientCall.objects.create(
        client=client,
        created_by=employee_a,
        notes="Follow-up call",
        call_datetime=assigned_at + timedelta(hours=1),
    )

    re_assign_inactive_clients()

    client.refresh_from_db()
    assert client.assigned_to_id == employee_a.id


@pytest.mark.django_db
def test_does_not_reassign_when_assignee_sent_whatsapp_since_assignment(
    reassign_company, employee_a, employee_b
):
    assigned_at = timezone.now() - timedelta(hours=48)
    client = Client.objects.create(
        name="WhatsApp Lead",
        company=reassign_company,
        assigned_to=employee_a,
        assigned_at=assigned_at,
    )
    LeadWhatsAppMessage.objects.create(
        client=client,
        phone_number="+9647000000000",
        body="Hello",
        direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
        created_by=employee_a,
    )

    re_assign_inactive_clients()

    client.refresh_from_db()
    assert client.assigned_to_id == employee_a.id


@pytest.mark.django_db
def test_reassigns_when_only_previous_assignee_contacted(
    reassign_company, employee_a, employee_b
):
    """Prior assignee activity must not protect the current assignee."""
    old_assigned_at = timezone.now() - timedelta(hours=72)
    new_assigned_at = timezone.now() - timedelta(hours=48)
    client = Client.objects.create(
        name="Transferred Lead",
        company=reassign_company,
        assigned_to=employee_b,
        assigned_at=new_assigned_at,
        last_contacted_at=old_assigned_at + timedelta(hours=1),
    )
    ClientCall.objects.create(
        client=client,
        created_by=employee_a,
        notes="Call while employee A was assigned",
        call_datetime=old_assigned_at + timedelta(hours=1),
    )

    re_assign_inactive_clients()

    client.refresh_from_db()
    assert client.assigned_to_id == employee_a.id
    assert ClientEvent.objects.filter(client=client, event_type="re_assignment").exists()


@pytest.mark.django_db
def test_reassigns_when_assignee_only_acted_before_current_assignment(
    reassign_company, employee_a, employee_b
):
    """Lifetime actions from a previous assignment stint must not lock the lead."""
    first_assigned_at = timezone.now() - timedelta(days=10)
    second_assigned_at = timezone.now() - timedelta(hours=48)
    client = Client.objects.create(
        name="Reassigned Back Lead",
        company=reassign_company,
        assigned_to=employee_a,
        assigned_at=second_assigned_at,
    )
    call = ClientCall.objects.create(
        client=client,
        created_by=employee_a,
        notes="Old stint call",
        call_datetime=first_assigned_at + timedelta(hours=1),
    )
    ClientCall.objects.filter(pk=call.pk).update(
        created_at=first_assigned_at + timedelta(hours=1)
    )

    re_assign_inactive_clients()

    client.refresh_from_db()
    assert client.assigned_to_id == employee_b.id
