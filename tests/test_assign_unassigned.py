"""Tests for bulk assign-unassigned API (per-lead least-busy distribution)."""
import pytest
from rest_framework import status

from conftest import api_body
from crm.models import Client


@pytest.mark.django_db
def test_assign_unassigned_picks_least_busy_per_lead(authenticated_admin, company):
    from accounts.models import User

    employees = [
        User.objects.create_user(
            username=f"spread{i}",
            email=f"spread{i}@test.com",
            password="x",
            company=company,
            role="employee",
            is_active=True,
        )
        for i in range(3)
    ]

    company.auto_assign_enabled = False
    company.save(update_fields=["auto_assign_enabled"])

    for index in range(6):
        Client.objects.create(
            name=f"Unassigned {index}",
            company=company,
            priority="low",
            type="cold",
        )

    company.auto_assign_enabled = True
    company.save(update_fields=["auto_assign_enabled"])

    response = authenticated_admin.post(
        "/api/v1/clients/assign_unassigned/", {}, format="json"
    )
    assert response.status_code == status.HTTP_200_OK
    payload = api_body(response)
    assert payload["assigned_count"] == 6

    assigned_ids = list(
        Client.objects.filter(company=company)
        .exclude(assigned_to__isnull=True)
        .values_list("assigned_to_id", flat=True)
    )
    assert len(assigned_ids) == 6
    assert len(set(assigned_ids)) >= 2
    for employee in employees:
        assert employee.id in assigned_ids
