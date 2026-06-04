"""
Platform maintenance mode: middleware, public status, management command.
"""

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from rest_framework import status

from settings.maintenance_policy import invalidate_maintenance_cache
from settings.models import SystemSettings


@pytest.fixture(autouse=True)
def _clear_maintenance_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def system_settings(db):
    s = SystemSettings.get_settings()
    s.maintenance_mode = False
    s.maintenance_message = "The system is under maintenance. Please try again later."
    s.save(update_fields=["maintenance_mode", "maintenance_message", "updated_at"])
    invalidate_maintenance_cache()
    return s


@pytest.mark.django_db
def test_public_maintenance_status_localized_ar(api_client, system_settings):
    system_settings.maintenance_mode = True
    system_settings.save(update_fields=["maintenance_mode", "updated_at"])
    invalidate_maintenance_cache()

    url = reverse("public_maintenance_status")
    r = api_client.get(url, HTTP_X_LANGUAGE="ar")
    assert r.status_code == status.HTTP_200_OK
    assert "النظام قيد الصيانة" in r.data["data"]["message"]


@pytest.mark.django_db
def test_public_maintenance_status_without_api_key(api_client, system_settings):
    url = reverse("public_maintenance_status")
    r = api_client.get(url)
    assert r.status_code == status.HTTP_200_OK
    assert r.data["success"] is True
    assert r.data["data"]["maintenance_mode"] is False


@pytest.mark.django_db
def test_middleware_blocks_api_when_maintenance_on(api_client, system_settings):
    system_settings.maintenance_mode = True
    system_settings.maintenance_message = "Upgrading now"
    system_settings.save(update_fields=["maintenance_mode", "maintenance_message", "updated_at"])
    invalidate_maintenance_cache()

    r = api_client.get("/api/v1/crm/feature-policies/")
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert r.json()["success"] is False
    assert r.json()["error"]["code"] == "maintenance_mode"
    assert r.json()["error"]["message"] == "Upgrading now"


@pytest.mark.django_db
def test_public_maintenance_status_still_works_when_on(api_client, system_settings):
    system_settings.maintenance_mode = True
    system_settings.save(update_fields=["maintenance_mode", "updated_at"])
    invalidate_maintenance_cache()

    url = reverse("public_maintenance_status")
    r = api_client.get(url)
    assert r.status_code == status.HTTP_200_OK
    assert r.data["data"]["maintenance_mode"] is True


@pytest.mark.django_db
def test_whitelisted_inbound_leads_path_not_blocked(api_client, system_settings):
    system_settings.maintenance_mode = True
    system_settings.save(update_fields=["maintenance_mode", "updated_at"])
    invalidate_maintenance_cache()

    r = api_client.post(
        "/api/v1/integrations/leads/inbound/",
        {},
        format="json",
    )
    assert r.status_code != status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.django_db
def test_management_command_on_and_off(system_settings):
    call_command("maintenance_mode", "--on", "--message", "CLI maintenance")
    system_settings.refresh_from_db()
    assert system_settings.maintenance_mode is True
    assert system_settings.maintenance_message == "CLI maintenance"

    call_command("maintenance_mode", "--off")
    system_settings.refresh_from_db()
    assert system_settings.maintenance_mode is False
