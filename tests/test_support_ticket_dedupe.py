"""Support ticket create dedupe, email deferral, and delete."""

from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models import User
from conftest import api_body
from support.models import SupportTicket


@pytest.mark.django_db
def test_duplicate_post_within_window_creates_one_ticket(authenticated_employee):
    """Two identical POSTs within 2 minutes collapse to a single ticket."""
    payload = {
        "title": "Calendar icon",
        "description": "The calendar icon is broken on the home screen.",
    }

    with patch("support.views._send_support_ticket_emails_async") as mock_emails:
        r1 = authenticated_employee.post(
            "/api/support-tickets/", payload, format="json"
        )
        r2 = authenticated_employee.post(
            "/api/support-tickets/", payload, format="json"
        )

    assert r1.status_code == status.HTTP_201_CREATED
    assert r2.status_code == status.HTTP_201_CREATED

    d1 = api_body(r1)
    d2 = api_body(r2)
    assert d1["id"] == d2["id"]
    assert SupportTicket.objects.filter(title=payload["title"]).count() == 1
    # Emails only for the real create, not the deduped retry.
    mock_emails.assert_called_once()


@pytest.mark.django_db
def test_distinct_title_creates_second_ticket(authenticated_employee):
    """A different title within the window still creates a new ticket."""
    with patch("support.views._send_support_ticket_emails_async") as mock_emails:
        r1 = authenticated_employee.post(
            "/api/support-tickets/",
            {"title": "Bug A", "description": "Same description"},
            format="json",
        )
        r2 = authenticated_employee.post(
            "/api/support-tickets/",
            {"title": "Bug B", "description": "Same description"},
            format="json",
        )

    assert r1.status_code == status.HTTP_201_CREATED
    assert r2.status_code == status.HTTP_201_CREATED
    assert api_body(r1)["id"] != api_body(r2)["id"]
    assert SupportTicket.objects.count() == 2
    assert mock_emails.call_count == 2


@pytest.mark.django_db
def test_employee_cannot_delete_support_ticket(
    authenticated_employee, employee_user, company
):
    ticket = SupportTicket.objects.create(
        title="Keep me",
        description="desc",
        company=company,
        created_by=employee_user,
    )
    r = authenticated_employee.delete(f"/api/support-tickets/{ticket.id}/")
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert SupportTicket.objects.filter(pk=ticket.id).exists()


@pytest.mark.django_db
def test_super_admin_can_delete_support_ticket(api_client, company, employee_user):
    super_admin = User.objects.create_user(
        username="platform_super",
        email="platform_super@example.com",
        password="testpass123",
        company=None,
        role="admin",
        is_superuser=True,
    )
    ticket = SupportTicket.objects.create(
        title="Delete me",
        description="desc",
        company=company,
        created_by=employee_user,
    )
    api_client.force_authenticate(user=super_admin)
    r = api_client.delete(f"/api/support-tickets/{ticket.id}/")
    assert r.status_code == status.HTTP_204_NO_CONTENT
    assert not SupportTicket.objects.filter(pk=ticket.id).exists()
