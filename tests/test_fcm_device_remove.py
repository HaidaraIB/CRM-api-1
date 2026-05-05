"""FCM device unregister (no JWT) for mobile session expiry / logout."""

import pytest
from django.urls import reverse
from rest_framework import status

from accounts.models import User


@pytest.mark.django_db
def test_remove_fcm_token_device_clears_legacy_and_list(api_client):
    token = "fcm_device_test_token_abc_unique"
    user = User.objects.create_user(
        username="fcm_device_user",
        email="fcmdevice@example.com",
        password="securepassword123",
        role="employee",
    )
    user.fcm_token = token
    user.fcm_tokens = [token, "other_kept"]
    user.save(update_fields=["fcm_token", "fcm_tokens"])

    url = reverse("remove_fcm_token_device")
    response = api_client.post(url, {"fcm_token": token}, format="json")

    assert response.status_code == status.HTTP_200_OK
    user.refresh_from_db()
    assert user.fcm_token is None or user.fcm_token == ""
    assert token not in (user.fcm_tokens or [])
    assert "other_kept" in (user.fcm_tokens or [])


@pytest.mark.django_db
def test_remove_fcm_token_device_requires_body(api_client):
    url = reverse("remove_fcm_token_device")
    response = api_client.post(url, {}, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body.get("success") is False
