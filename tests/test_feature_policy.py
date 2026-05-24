"""Feature policy tests for field visits."""
import base64

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status

from conftest import api_body
from settings.feature_policy import (
    FIELD_VISIT_FEATURE,
    get_effective_feature_policy,
    get_field_visit_access,
)
from settings.models import SystemSettings

MINI_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.django_db
def test_field_visit_blocked_when_admin_disables_globally(authenticated_admin, company):
    from crm.models import Client

    settings_obj = SystemSettings.get_settings()
    settings_obj.feature_policies = {
        FIELD_VISIT_FEATURE: {
            "global_enabled": False,
            "global_message": "Disabled by admin",
            "company_overrides": {},
        }
    }
    settings_obj.save(update_fields=["feature_policies"])

    client = Client.objects.create(
        name="Policy Lead",
        company=company,
        priority="medium",
        type="fresh",
    )

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "On-site",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": "33.315200",
            "employee_longitude": "44.366100",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_field_visit_list_blocked_when_disabled(authenticated_admin, company):
    settings_obj = SystemSettings.get_settings()
    settings_obj.feature_policies = {
        FIELD_VISIT_FEATURE: {
            "global_enabled": False,
            "global_message": "Disabled by admin",
            "company_overrides": {},
        }
    }
    settings_obj.save(update_fields=["feature_policies"])

    response = authenticated_admin.get("/api/v1/client-field-visits/")
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_owner_can_disable_field_visits_for_company(authenticated_admin, company):
    company.field_visit_enabled = False
    company.save(update_fields=["field_visit_enabled"])

    access = get_field_visit_access(company)
    assert access["enabled"] is False
    assert access["scope"] == "company_setting"


@pytest.mark.django_db
def test_update_field_visit_settings_requires_admin_policy(authenticated_admin, company):
    settings_obj = SystemSettings.get_settings()
    settings_obj.feature_policies = {
        FIELD_VISIT_FEATURE: {
            "global_enabled": False,
            "global_message": "Disabled by admin",
            "company_overrides": {},
        }
    }
    settings_obj.save(update_fields=["feature_policies"])

    response = authenticated_admin.patch(
        f"/api/v1/companies/{company.id}/update_field_visit_settings/",
        {"field_visit_enabled": True},
        format="json",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_feature_policy_view_returns_effective_access(authenticated_admin, company):
    response = authenticated_admin.get("/api/v1/crm/feature-policies/")
    assert response.status_code == status.HTTP_200_OK
    data = api_body(response)
    assert FIELD_VISIT_FEATURE in data
    assert data[FIELD_VISIT_FEATURE]["enabled"] is True


@pytest.mark.django_db
def test_field_visit_with_photo_still_works_when_enabled(authenticated_admin, company):
    from crm.models import Client

    client = Client.objects.create(
        name="Photo Lead",
        company=company,
        priority="medium",
        type="fresh",
    )
    photo = SimpleUploadedFile("site.png", MINI_PNG_BYTES, content_type="image/png")

    response = authenticated_admin.post(
        "/api/v1/client-field-visits/",
        {
            "client": client.id,
            "summary": "With photo",
            "visit_datetime": timezone.now().isoformat(),
            "employee_latitude": "33.315200",
            "employee_longitude": "44.366100",
            "client_location_photo": photo,
        },
        format="multipart",
    )
    assert response.status_code == status.HTTP_201_CREATED
