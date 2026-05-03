"""Tests for weekly day off blocking on lead/deal assignment."""
import json

import pytest
from rest_framework import status

from conftest import api_body
from crm.models import Client, Deal
from crm.signals import get_least_busy_employee


def _error_payload(response):
    raw = getattr(response, "data", None)
    if raw is None:
        raw = json.loads(response.content.decode())
    return raw


@pytest.mark.django_db
def test_get_least_busy_skips_employee_on_weekly_off(monkeypatch, company):
    from accounts.models import User

    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 2)

    emp_off = User.objects.create_user(
        username="e_off",
        email="e_off@test.com",
        password="x",
        company=company,
        role="employee",
        is_active=True,
        weekly_day_off=2,
    )
    emp_on = User.objects.create_user(
        username="e_on",
        email="e_on@test.com",
        password="x",
        company=company,
        role="employee",
        is_active=True,
        weekly_day_off=4,
    )
    # Seed workload: assignee is on day off today, so normal save would reject.
    c1 = Client(name="c1", company=company, assigned_to=emp_off)
    c1.save(_skip_assignee_availability_check=True)
    assert get_least_busy_employee(company) == emp_on


@pytest.mark.django_db
def test_get_least_busy_returns_none_when_all_on_off(monkeypatch, company):
    from accounts.models import User

    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 1)
    for i in range(2):
        User.objects.create_user(
            username=f"eo{i}",
            email=f"eo{i}@test.com",
            password="x",
            company=company,
            role="employee",
            is_active=True,
            weekly_day_off=1,
        )
    assert get_least_busy_employee(company) is None


@pytest.mark.django_db
def test_create_client_rejects_assigned_to_on_day_off(
    monkeypatch, authenticated_admin, company, employee_user
):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 3)
    employee_user.weekly_day_off = 3
    employee_user.save(update_fields=["weekly_day_off"])

    response = authenticated_admin.post(
        "/api/v1/clients/",
        {
            "name": "Blocked",
            "priority": "low",
            "type": "cold",
            "company": company.id,
            "assigned_to": employee_user.id,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    err = _error_payload(response)
    assert err.get("success") is False
    assert err["error"]["code"] == "employee_weekly_day_off"


@pytest.mark.django_db
def test_patch_client_rejects_assigned_to_on_day_off(
    monkeypatch, authenticated_admin, company, employee_user
):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 0)
    employee_user.weekly_day_off = 0
    employee_user.save(update_fields=["weekly_day_off"])

    client = Client.objects.create(
        name="Lead", company=company, priority="low", type="cold"
    )
    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/",
        {"assigned_to": employee_user.id},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_payload(response)["error"]["code"] == "employee_weekly_day_off"


@pytest.mark.django_db
def test_patch_client_allows_same_assignee_on_day_off_when_editing_other_fields(
    monkeypatch, authenticated_admin, company, employee_user
):
    """Re-sending the same assignee must not block edits on that employee's day off."""
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 3)
    employee_user.weekly_day_off = 3
    employee_user.save(update_fields=["weekly_day_off"])

    client = Client(
        name="Lead",
        company=company,
        priority="low",
        type="cold",
        assigned_to=employee_user,
    )
    client.save(_skip_assignee_availability_check=True)

    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/",
        {"name": "Updated name", "assigned_to": employee_user.id},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert api_body(response)["name"] == "Updated name"
    assert api_body(response)["assigned_to"] == employee_user.id


@pytest.mark.django_db
def test_patch_deal_allows_same_employee_on_day_off_when_editing_other_fields(
    monkeypatch, authenticated_admin, company, admin_user, employee_user
):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 4)
    employee_user.weekly_day_off = 4
    employee_user.save(update_fields=["weekly_day_off"])

    client = Client.objects.create(
        name="C", company=company, priority="low", type="cold"
    )
    deal = Deal.objects.create(
        client=client,
        company=company,
        employee=employee_user,
        started_by=admin_user,
        stage="in_progress",
    )
    response = authenticated_admin.patch(
        f"/api/v1/deals/{deal.id}/",
        {"description": "Still on deal", "employee": employee_user.id},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert api_body(response)["description"] == "Still on deal"


@pytest.mark.django_db
def test_bulk_assign_rejects_target_on_day_off(
    monkeypatch, authenticated_admin, company, employee_user
):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 5)
    employee_user.weekly_day_off = 5
    employee_user.save(update_fields=["weekly_day_off"])

    c = Client.objects.create(name="Z", company=company, priority="low", type="cold")
    response = authenticated_admin.post(
        "/api/v1/clients/bulk_assign/",
        {"client_ids": [c.id], "user_id": employee_user.id},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_payload(response)["error"]["code"] == "employee_weekly_day_off"


@pytest.mark.django_db
def test_create_deal_rejects_employee_on_day_off(
    monkeypatch, authenticated_admin, company, admin_user, employee_user
):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 6)
    employee_user.weekly_day_off = 6
    employee_user.save(update_fields=["weekly_day_off"])

    client = Client.objects.create(
        name="C", company=company, priority="low", type="cold"
    )
    response = authenticated_admin.post(
        "/api/v1/deals/",
        {
            "client": client.id,
            "company": company.id,
            "employee": employee_user.id,
            "stage": "in_progress",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _error_payload(response)["error"]["code"] == "employee_weekly_day_off"


@pytest.mark.django_db
def test_assign_unassigned_no_employee_when_all_on_day_off(
    monkeypatch, authenticated_admin, company, employee_user
):
    from accounts.models import User

    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 2)
    employee_user.weekly_day_off = 2
    employee_user.save(update_fields=["weekly_day_off"])
    User.objects.create_user(
        username="e2",
        email="e2@test.com",
        password="x",
        company=company,
        role="employee",
        is_active=True,
        weekly_day_off=2,
    )

    company.auto_assign_enabled = True
    company.save(update_fields=["auto_assign_enabled"])

    Client.objects.create(name="U", company=company, priority="low", type="cold")
    response = authenticated_admin.post(
        "/api/v1/clients/assign_unassigned/", {}, format="json"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    err = _error_payload(response)
    assert err.get("success") is False
    assert err["error"]["code"] == "no_available_employees_day_off"


@pytest.mark.django_db
def test_create_client_allows_assigned_when_not_day_off(
    monkeypatch, authenticated_admin, company, employee_user
):
    monkeypatch.setattr("crm.availability.local_today_weekday", lambda c: 0)
    employee_user.weekly_day_off = 3
    employee_user.save(update_fields=["weekly_day_off"])

    response = authenticated_admin.post(
        "/api/v1/clients/",
        {
            "name": "OK",
            "priority": "low",
            "type": "cold",
            "company": company.id,
            "assigned_to": employee_user.id,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert api_body(response)["assigned_to"] == employee_user.id
