"""Tests for completing open calendar follow-ups when a new call is logged."""
# pylint: disable=no-member

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from crm.models import Client, ClientCall, ClientTask, complete_open_contact_items_for_client
from settings.models import LeadStage

User = get_user_model()


@pytest.mark.django_db
def test_new_call_completes_open_follow_ups_and_reminders(company):
    employee = User.objects.create_user(
        username="caller",
        email="caller@test.com",
        role="employee",
        company=company,
        is_active=True,
    )
    client = Client.objects.create(name="Queue Lead", company=company, assigned_to=employee)
    stage = LeadStage.objects.create(name="Follow", company=company)

    today = timezone.now()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    # Create without follow-up/reminder dates first so create signals do not clear them
    open_call = ClientCall.objects.create(
        client=client,
        created_by=employee,
        notes="No answer",
    )
    future_call = ClientCall.objects.create(
        client=client,
        created_by=employee,
        notes="Future",
    )
    open_task = ClientTask.objects.create(
        client=client,
        created_by=employee,
        stage=stage,
        notes="Reminder",
    )
    ClientCall.objects.filter(pk=open_call.pk).update(follow_up_date=yesterday)
    ClientCall.objects.filter(pk=future_call.pk).update(follow_up_date=tomorrow)
    ClientTask.objects.filter(pk=open_task.pk).update(reminder_date=today)

    # Creating a new call triggers the post_save signal
    new_call = ClientCall.objects.create(
        client=client,
        created_by=employee,
        notes="Reached them",
        follow_up_date=tomorrow,
    )

    open_call.refresh_from_db()
    future_call.refresh_from_db()
    open_task.refresh_from_db()
    new_call.refresh_from_db()

    assert open_call.follow_up_completed_at is not None
    assert open_task.reminder_completed_at is not None
    # Future follow-ups and the new call's own follow-up stay open
    assert future_call.follow_up_completed_at is None
    assert new_call.follow_up_completed_at is None


@pytest.mark.django_db
def test_complete_open_contact_items_excludes_new_call(company):
    employee = User.objects.create_user(
        username="caller2",
        email="caller2@test.com",
        role="employee",
        company=company,
        is_active=True,
    )
    client = Client.objects.create(name="Exclude Lead", company=company, assigned_to=employee)
    due_today = ClientCall.objects.create(
        client=client,
        created_by=employee,
        notes="Due",
    )
    ClientCall.objects.filter(pk=due_today.pk).update(follow_up_date=timezone.now())
    due_today.refresh_from_db()

    complete_open_contact_items_for_client(client.id, exclude_call_id=due_today.pk)
    due_today.refresh_from_db()
    assert due_today.follow_up_completed_at is None

    complete_open_contact_items_for_client(client.id)
    due_today.refresh_from_db()
    assert due_today.follow_up_completed_at is not None
