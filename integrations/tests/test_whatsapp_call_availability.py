"""Unit tests for WhatsApp call Away status and call hours."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.models import (
    IntegrationAccount,
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallStatus,
)
from integrations.services.whatsapp_call_availability import (
    is_within_call_hours,
    normalize_weekly_schedule,
    set_agent_call_away,
    user_is_whatsapp_call_away,
)


@pytest.fixture
def wa_account(company):
    account = IntegrationAccount.objects.create(
        company=company,
        platform="whatsapp",
        name="WA Hours Test",
        status="connected",
    )
    account.set_access_token("test-wa-hours-token")
    account.save(update_fields=["access_token"])
    return WhatsAppAccount.objects.create(
        company=company,
        waba_id="waba_hours",
        phone_number_id="pid_hours_1",
        display_phone_number="+15550002222",
        status="connected",
        calling_enabled=True,
    )


def test_normalize_weekly_schedule_defaults_closed():
    weekly = normalize_weekly_schedule({})
    assert weekly["sunday"]["closed"] is True
    assert weekly["monday"]["open"] == "09:00"


def test_within_hours_disabled_is_always_open(wa_account):
    wa_account.call_hours_enabled = False
    assert is_within_call_hours(wa_account) is True


@pytest.mark.django_db
def test_within_hours_monday_window(company, wa_account):
    wa_account.call_hours_enabled = True
    wa_account.call_hours_timezone = "UTC"
    wa_account.call_hours_weekly = {
        "monday": {"closed": False, "open": "09:00", "close": "17:00"},
        "tuesday": {"closed": True},
        "wednesday": {"closed": True},
        "thursday": {"closed": True},
        "friday": {"closed": True},
        "saturday": {"closed": True},
        "sunday": {"closed": True},
    }
    wa_account.save()
    monday_noon = datetime(2026, 8, 10, 12, 0, tzinfo=ZoneInfo("UTC"))  # Monday
    monday_evening = datetime(2026, 8, 10, 20, 0, tzinfo=ZoneInfo("UTC"))
    assert is_within_call_hours(wa_account, when=monday_noon) is True
    assert is_within_call_hours(wa_account, when=monday_evening) is False


@pytest.mark.django_db
def test_agent_away_status_endpoint(company, employee_user, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])

    client = APIClient()
    client.force_authenticate(user=employee_user)
    res = client.post(
        reverse("whatsapp_call_agent_status"),
        {"status": "away", "duration_minutes": 15},
        format="json",
    )
    assert res.status_code == 200
    employee_user.refresh_from_db()
    assert user_is_whatsapp_call_away(employee_user) is True
    data = res.json().get("data") or res.json()
    assert data.get("status") == "away"

    res2 = client.post(
        reverse("whatsapp_call_agent_status"),
        {"status": "ready"},
        format="json",
    )
    assert res2.status_code == 200
    employee_user.refresh_from_db()
    assert user_is_whatsapp_call_away(employee_user) is False


@pytest.mark.django_db
def test_pending_hides_rings_when_away(
    company, wa_account, employee_user, plan, subscription
):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    set_agent_call_away(employee_user, duration_minutes=30)

    WhatsAppCall.objects.create(
        company=company,
        whatsapp_account=wa_account,
        meta_call_id="wacid.away.1",
        direction=WhatsAppCallDirection.INBOUND,
        status=WhatsAppCallStatus.RINGING,
        peer_phone="15550009999",
    )
    client = APIClient()
    client.force_authenticate(user=employee_user)
    res = client.get(reverse("whatsapp_calls_pending"))
    assert res.status_code == 200
    data = res.json().get("data") or res.json()
    assert data.get("results") == []
    assert (data.get("agent_status") or {}).get("status") == "away"


@pytest.mark.django_db
def test_call_hours_get_put(company, wa_account, admin_user, plan, subscription):
    plan.features = {**(plan.features or {}), "integration_whatsapp": True}
    plan.save(update_fields=["features"])
    client = APIClient()
    client.force_authenticate(user=admin_user)
    res = client.get(reverse("whatsapp_call_hours"))
    assert res.status_code == 200

    res2 = client.put(
        reverse("whatsapp_call_hours"),
        {
            "enabled": True,
            "timezone": "Asia/Baghdad",
            "weekly": {
                "monday": {"closed": False, "open": "09:00", "close": "17:00"},
                "tuesday": {"closed": True},
                "wednesday": {"closed": True},
                "thursday": {"closed": True},
                "friday": {"closed": True},
                "saturday": {"closed": True},
                "sunday": {"closed": True},
            },
            "out_of_hours_message": "We are closed now.",
            "sync_meta": False,
        },
        format="json",
    )
    assert res2.status_code == 200
    wa_account.refresh_from_db()
    assert wa_account.call_hours_enabled is True
    assert wa_account.call_hours_timezone == "Asia/Baghdad"
    assert wa_account.out_of_hours_message == "We are closed now."
