"""Catalog endpoint and validation error envelope."""

import pytest
from rest_framework.exceptions import ErrorDetail, ValidationError

from crm_saas_api.exception_handler import custom_exception_handler
from crm_saas_api.responses import validation_error_response


@pytest.mark.django_db
def test_anonymous_catalog_is_public_only(api_client):
    response = api_client.get("/api/v1/validation/catalog/")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["version"]
    assert "auth.login" in payload["forms"]
    assert "lead.upsert" not in payload["forms"]
    assert response["ETag"]


@pytest.mark.django_db
def test_anonymous_cannot_read_private_form(api_client):
    response = api_client.get("/api/v1/validation/catalog/", {"forms": "lead.upsert"})
    assert response.status_code == 401
    assert response.data["error"]["code"] == "authentication_failed"


@pytest.mark.django_db
def test_authenticated_catalog_includes_context(authenticated_admin):
    response = authenticated_admin.get("/api/v1/validation/catalog/", {"forms": "lead.upsert,auth.login"})
    assert response.status_code == 200
    payload = response.json()["data"]
    assert "lead.upsert" in payload["forms"]
    assert "auth.login" in payload["forms"]
    assert "hasCompany" in payload["context"]


@pytest.mark.django_db
def test_catalog_etag_not_modified(api_client):
    first = api_client.get("/api/v1/validation/catalog/", {"forms": "auth.login"})
    etag = first["ETag"]
    second = api_client.get(
        "/api/v1/validation/catalog/",
        {"forms": "auth.login"},
        HTTP_IF_NONE_MATCH=etag,
    )
    assert second.status_code == 304


def test_exception_handler_emits_field_codes():
    exc = ValidationError({"name": [ErrorDetail("This field is required.", code="required")]})
    response = custom_exception_handler(exc, {})
    assert response.status_code == 400
    assert response.data["error"]["details"]["name"]
    fields = response.data["error"]["fields"]
    assert fields["name"][0]["code"] == "validation.required"


def test_validation_error_response_includes_fields():
    response = validation_error_response(
        {"email": [ErrorDetail("Enter a valid email address.", code="invalid")]}
    )
    assert response.data["error"]["code"] == "validation_error"
    assert response.data["error"]["fields"]["email"][0]["code"] == "validation.invalid"
    assert "email" in response.data["error"]["details"]
