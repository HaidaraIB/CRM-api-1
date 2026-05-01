import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from accounts.email_registration_policy import EMAIL_VERIFICATION_REQUIRED_CACHE_KEY
from accounts.email_registration_utils import sign_email_registration_token

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_registration_email_requirement_settings_roundtrip(api_client):
    admin = User.objects.create_superuser(
        username="email_req_admin",
        email="email_req_admin@test.com",
        password="secret12345",
    )
    api_client.force_authenticate(user=admin)
    url = reverse("registration_email_requirement_settings")

    r1 = api_client.post(url, {"email_verification_required": True}, format="json")
    assert r1.status_code == status.HTTP_200_OK
    assert r1.data["data"]["email_verification_required"] is True
    assert cache.get(EMAIL_VERIFICATION_REQUIRED_CACHE_KEY) is True

    r2 = api_client.get(url)
    assert r2.status_code == status.HTTP_200_OK
    assert r2.data["data"]["email_verification_required"] is True


@pytest.mark.django_db
def test_register_company_rejects_when_email_verification_required_and_token_missing(api_client):
    cache.set(EMAIL_VERIFICATION_REQUIRED_CACHE_KEY, True, timeout=None)
    url = reverse("register_company")
    payload = {
        "company": {
            "name": "Email Req Co",
            "domain": "email-req-co",
            "specialization": "services",
        },
        "owner": {
            "first_name": "Test",
            "last_name": "Owner",
            "email": "owner@email-req-co.com",
            "username": "owner_email_req",
            "password": "StrongPass123!",
            "phone": "+9647701234567",
        },
    }

    r = api_client.post(url, payload, format="json")
    assert r.status_code == status.HTTP_400_BAD_REQUEST
    assert r.data["success"] is False
    assert "email_verification_token" in r.data["error"]["details"]


@pytest.mark.django_db
def test_register_company_accepts_when_email_verification_token_is_valid(api_client):
    cache.set(EMAIL_VERIFICATION_REQUIRED_CACHE_KEY, True, timeout=None)
    email = "owner@verified-reg.com"
    token = sign_email_registration_token(email)
    url = reverse("register_company")
    payload = {
        "company": {
            "name": "Email Verified Co",
            "domain": "email-verified-co",
            "specialization": "services",
        },
        "owner": {
            "first_name": "Test",
            "last_name": "Owner",
            "email": email,
            "username": "owner_email_verified",
            "password": "StrongPass123!",
            "phone": "+9647711234567",
        },
        "email_verification_token": token,
    }

    r = api_client.post(url, payload, format="json")
    assert r.status_code == status.HTTP_201_CREATED
    assert r.data["success"] is True
    owner = User.objects.get(username="owner_email_verified")
    assert owner.email_verified is True
