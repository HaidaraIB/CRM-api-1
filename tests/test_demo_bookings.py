"""Tests for public demo booking and admin management."""

from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework import status

from demo_bookings.models import (
    DEFAULT_WEEKLY_HOURS,
    DemoBooking,
    DemoBookingBlockedDate,
    DemoBookingSettings,
    DemoBookingStatus,
)
from demo_bookings.services import compute_available_slots, is_slot_available

User = get_user_model()


@pytest.fixture
def demo_settings(db):
    settings_obj = DemoBookingSettings.get_settings()
    settings_obj.is_enabled = True
    settings_obj.timezone = "Asia/Baghdad"
    settings_obj.duration_minutes = 30
    settings_obj.horizon_days = 14
    settings_obj.min_notice_hours = 0
    settings_obj.weekly_hours = dict(DEFAULT_WEEKLY_HOURS)
    settings_obj.intro_en = "Book a walkthrough"
    settings_obj.intro_ar = "احجز جلسة"
    settings_obj.save()
    return settings_obj


@pytest.fixture
def super_admin(db):
    return User.objects.create_user(
        username="platform_admin",
        email="admin@loop.test",
        password="testpass123",
        is_superuser=True,
        is_staff=True,
    )


def _auth(client, user):
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")


@pytest.mark.django_db
def test_public_config_when_disabled(api_client, demo_settings):
    demo_settings.is_enabled = False
    demo_settings.save()
    response = api_client.get("/api/v1/public/demo-bookings/config/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["is_enabled"] is False


@pytest.mark.django_db
def test_slots_respect_blocked_date(api_client, demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    DemoBookingBlockedDate.objects.create(date=today, reason="Holiday")
    response = api_client.get(
        "/api/v1/public/demo-bookings/slots/",
        {"from": today.isoformat(), "to": today.isoformat()},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["slots"] == []


@pytest.mark.django_db
def test_create_booking_and_double_book(api_client, demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    assert slots, "Expected at least one slot for testing"
    starts_at = slots[0]["starts_at"]
    payload = {
        "starts_at": starts_at,
        "name": "Visitor One",
        "email": "visitor@example.com",
        "phone": "+9647700000001",
        "language": "en",
    }
    first = api_client.post("/api/v1/public/demo-bookings/", payload, format="json")
    assert first.status_code == status.HTTP_201_CREATED
    assert first.json()["data"]["status"] == DemoBookingStatus.PENDING
    if connection.vendor == "postgresql":
        assert first.json()["data"]["id"] >= 1000
    payload_dup_slot = {
        **payload,
        "email": "visitor2@example.com",
        "phone": "+9647700000010",
    }
    second = api_client.post("/api/v1/public/demo-bookings/", payload_dup_slot, format="json")
    assert second.status_code == status.HTTP_409_CONFLICT


@pytest.mark.django_db
def test_duplicate_upcoming_email_rejected(api_client, demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    assert len(slots) >= 2
    payload1 = {
        "starts_at": slots[0]["starts_at"],
        "name": "Visitor",
        "email": "same@example.com",
        "phone": "+9647700000002",
    }
    assert api_client.post("/api/v1/public/demo-bookings/", payload1, format="json").status_code == 201
    payload2 = {
        "starts_at": slots[1]["starts_at"],
        "name": "Visitor",
        "email": "same@example.com",
        "phone": "+9647700000099",
    }
    resp = api_client.post("/api/v1/public/demo-bookings/", payload2, format="json")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert resp.json()["error"]["code"] == "duplicate_booking"


@pytest.mark.django_db
def test_public_lookup_booking(api_client, demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    assert slots
    payload = {
        "starts_at": slots[0]["starts_at"],
        "name": "Lookup Guest",
        "email": "lookup@example.com",
        "phone": "+9647700000044",
        "language": "en",
    }
    created = api_client.post("/api/v1/public/demo-bookings/", payload, format="json")
    assert created.status_code == status.HTTP_201_CREATED
    booking_id = created.json()["data"]["id"]
    ok = api_client.post(
        "/api/v1/public/demo-bookings/lookup/",
        {"booking_id": booking_id, "email": "lookup@example.com"},
        format="json",
    )
    assert ok.status_code == status.HTTP_200_OK
    assert ok.json()["data"]["email"] == "lookup@example.com"
    assert ok.json()["data"]["timezone"] == "Asia/Baghdad"
    wrong_email = api_client.post(
        "/api/v1/public/demo-bookings/lookup/",
        {"booking_id": booking_id, "email": "wrong@example.com"},
        format="json",
    )
    assert wrong_email.status_code == status.HTTP_404_NOT_FOUND
    assert wrong_email.json()["error"]["code"] == "booking_not_found"


@pytest.mark.django_db
def test_booking_disabled_returns_403(api_client, demo_settings):
    demo_settings.is_enabled = False
    demo_settings.save()
    response = api_client.post(
        "/api/v1/public/demo-bookings/",
        {
            "starts_at": timezone.now().isoformat(),
            "name": "X",
            "email": "x@example.com",
            "phone": "+9647700000003",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_admin_list_requires_permission(api_client, demo_settings, super_admin):
    response = api_client.get("/api/v1/demo-bookings/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    _auth(api_client, super_admin)
    response = api_client.get("/api/v1/demo-bookings/")
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_admin_patch_status(api_client, demo_settings, super_admin):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    starts = datetime.fromisoformat(slots[0]["starts_at"].replace("Z", "+00:00"))
    booking = DemoBooking.objects.create(
        name="Test",
        email="t@example.com",
        phone="+9647700000004",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=30),
        status=DemoBookingStatus.CONFIRMED,
    )
    _auth(api_client, super_admin)
    response = api_client.patch(
        f"/api/v1/demo-bookings/{booking.id}/",
        {"status": "completed"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    booking.refresh_from_db()
    assert booking.status == DemoBookingStatus.COMPLETED


@pytest.mark.django_db
def test_admin_delete_booking(api_client, demo_settings, super_admin):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    starts = datetime.fromisoformat(slots[0]["starts_at"].replace("Z", "+00:00"))
    booking = DemoBooking.objects.create(
        name="Delete Me",
        email="del@example.com",
        phone="+9647700000005",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=30),
        status=DemoBookingStatus.CONFIRMED,
    )
    _auth(api_client, super_admin)
    response = api_client.delete(f"/api/v1/demo-bookings/{booking.id}/")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not DemoBooking.objects.filter(pk=booking.id).exists()


@pytest.mark.django_db
def test_is_slot_available_matches_engine(demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    if not slots:
        pytest.skip("No slots in current window")
    starts = datetime.fromisoformat(slots[0]["starts_at"].replace("Z", "+00:00"))
    assert is_slot_available(demo_settings, starts)


@pytest.mark.django_db
def test_create_rejects_empty_phone(api_client, demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    if not slots:
        pytest.skip("No slots in current window")
    resp = api_client.post(
        "/api/v1/public/demo-bookings/",
        {
            "starts_at": slots[0]["starts_at"],
            "name": "No Phone",
            "email": "nophone@example.com",
            "phone": "   ",
        },
        format="json",
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_pending_booking_hides_slot(api_client, demo_settings):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    if not slots:
        pytest.skip("No slots in current window")
    starts_at = slots[0]["starts_at"]
    api_client.post(
        "/api/v1/public/demo-bookings/",
        {
            "starts_at": starts_at,
            "name": "Hold Slot",
            "email": "hold@example.com",
            "phone": "+9647700000088",
        },
        format="json",
    )
    after = compute_available_slots(demo_settings, today, today)
    assert not any(s["starts_at"] == starts_at for s in after)


@pytest.mark.django_db
@patch("demo_bookings.notifications.send_admin_message", return_value=(True, {}))
@patch("accounts.event_emails._send_raw_event_email", return_value=True)
def test_admin_approve_pending(
    _mock_email, _mock_wa, api_client, demo_settings, super_admin
):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    assert slots
    created = api_client.post(
        "/api/v1/public/demo-bookings/",
        {
            "starts_at": slots[0]["starts_at"],
            "name": "Approve Me",
            "email": "approve@example.com",
            "phone": "+9647700000077",
        },
        format="json",
    )
    booking_id = created.json()["data"]["id"]
    _auth(api_client, super_admin)
    response = api_client.post(f"/api/v1/demo-bookings/{booking_id}/approve/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["status"] == DemoBookingStatus.CONFIRMED


@pytest.mark.django_db
@patch("demo_bookings.notifications.send_admin_message", return_value=(True, {}))
@patch("accounts.event_emails._send_raw_event_email", return_value=True)
def test_admin_not_confirm_pending(
    _mock_email, _mock_wa, api_client, demo_settings, super_admin
):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    assert len(slots) >= 1
    created = api_client.post(
        "/api/v1/public/demo-bookings/",
        {
            "starts_at": slots[0]["starts_at"],
            "name": "Soft No",
            "email": "softno@example.com",
            "phone": "+9647700000066",
        },
        format="json",
    )
    booking_id = created.json()["data"]["id"]
    _auth(api_client, super_admin)
    response = api_client.post(f"/api/v1/demo-bookings/{booking_id}/not-confirm/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["status"] == DemoBookingStatus.NOT_CONFIRMED


@pytest.mark.django_db
def test_admin_cannot_patch_pending_to_confirmed(api_client, demo_settings, super_admin):
    tz = ZoneInfo("Asia/Baghdad")
    today = timezone.now().astimezone(tz).date()
    slots = compute_available_slots(demo_settings, today, today)
    starts = datetime.fromisoformat(slots[0]["starts_at"].replace("Z", "+00:00"))
    booking = DemoBooking.objects.create(
        name="Pending",
        email="pending@example.com",
        phone="+9647700000055",
        starts_at=starts,
        ends_at=starts + timedelta(minutes=30),
        status=DemoBookingStatus.PENDING,
    )
    _auth(api_client, super_admin)
    response = api_client.patch(
        f"/api/v1/demo-bookings/{booking.id}/",
        {"status": "confirmed"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
