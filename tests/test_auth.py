"""
Tests for authentication flows in the CRM API.
"""

import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework import status

User = get_user_model()


@pytest.mark.django_db
def test_login_with_username(api_client):
    """Test that a user can log in with their username and password."""
    User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="securepassword123",
    )
    url = reverse("token_obtain_pair")
    data = {"username": "testuser", "password": "securepassword123"}
    response = api_client.post(url, data)
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data


@pytest.mark.django_db
def test_login_with_email(api_client):
    """Test that a user can log in using their email address instead of username."""
    User.objects.create_user(
        username="testuser2",
        email="testuser2@example.com",
        password="securepassword123",
    )
    url = reverse("token_obtain_pair")
    data = {"username": "testuser2@example.com", "password": "securepassword123"}
    response = api_client.post(url, data)
    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data


@pytest.mark.django_db
def test_login_invalid_password(api_client):
    """Test that login fails gracefully with invalid credentials."""
    User.objects.create_user(
        username="testuser3",
        password="securepassword123",
    )
    url = reverse("token_obtain_pair")
    data = {"username": "testuser3", "password": "wrongpassword"}
    response = api_client.post(url, data)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
