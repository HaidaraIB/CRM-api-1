"""Tests for POST /api/v1/tasks/{id}/complete/."""
# pylint: disable=no-member

import pytest
from rest_framework import status

from crm.models import Client, Deal, Task


@pytest.mark.django_db
def test_complete_deal_task(authenticated_admin, company, admin_user):
    client = Client.objects.create(
        name="Deal Lead", company=company, priority="low", type="cold"
    )
    deal = Deal.objects.create(
        client=client, company=company, employee=admin_user, stage="in_progress"
    )
    task = Task.objects.create(deal=deal, notes="Follow up")

    response = authenticated_admin.post(f"/api/v1/tasks/{task.id}/complete/")
    assert response.status_code == status.HTTP_200_OK

    task.refresh_from_db()
    assert task.completed_at is not None

    # Idempotent
    again = authenticated_admin.post(f"/api/v1/tasks/{task.id}/complete/")
    assert again.status_code == status.HTTP_200_OK
    first_completed = task.completed_at
    task.refresh_from_db()
    assert task.completed_at == first_completed
