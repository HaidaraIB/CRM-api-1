"""Tests for GET /api/v1/activities/ (merged client tasks + client calls)."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from crm.models import Client, ClientCall, ClientTask
from settings.models import CallMethod, LeadStage


@pytest.mark.django_db
class TestActivitiesList:
    def test_returns_merged_tasks_and_calls_sorted_newest_first(
        self, authenticated_admin, company, admin_user
    ):
        lead = Client.objects.create(
            name="Lead One",
            company=company,
            priority="low",
            type="fresh",
        )
        stage = LeadStage.objects.create(company=company, name="following", color="#000")
        call_method = CallMethod.objects.create(company=company, name="phone", color="#111")

        older_task = ClientTask.objects.create(
            client=lead,
            stage=stage,
            notes="older task",
            created_by=admin_user,
        )
        newer_call = ClientCall.objects.create(
            client=lead,
            call_method=call_method,
            notes="newer call",
            created_by=admin_user,
        )
        now = timezone.now()
        ClientTask.objects.filter(pk=older_task.pk).update(created_at=now - timedelta(days=2))
        ClientCall.objects.filter(pk=newer_call.pk).update(created_at=now - timedelta(hours=1))

        response = authenticated_admin.get("/api/v1/activities/?page=1&page_size=20")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()["data"]
        assert body["count"] == 2
        assert len(body["results"]) == 2
        assert body["results"][0]["type"] == "client_call"
        assert body["results"][0]["lead"] == "Lead One"
        assert body["results"][1]["type"] == "client_task"

    def test_pagination_limits_page_size(self, authenticated_admin, company, admin_user):
        lead = Client.objects.create(
            name="Lead Paginated",
            company=company,
            priority="low",
            type="fresh",
        )
        for i in range(5):
            ClientTask.objects.create(
                client=lead,
                notes=f"task {i}",
                created_by=admin_user,
            )

        response = authenticated_admin.get("/api/v1/activities/?page=1&page_size=2")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()["data"]
        assert body["count"] == 5
        assert len(body["results"]) == 2
        assert body["next"] is not None

        page2 = authenticated_admin.get("/api/v1/activities/?page=2&page_size=2")
        assert page2.status_code == status.HTTP_200_OK
        body2 = page2.json()["data"]
        assert len(body2["results"]) == 2
        assert body2["previous"] is not None

    def test_user_filter(self, authenticated_admin, company, admin_user, employee_user):
        lead = Client.objects.create(
            name="Filtered Lead",
            company=company,
            priority="low",
            type="fresh",
        )
        ClientTask.objects.create(client=lead, notes="admin task", created_by=admin_user)
        ClientTask.objects.create(client=lead, notes="employee task", created_by=employee_user)

        response = authenticated_admin.get(
            f"/api/v1/activities/?user={employee_user.id}"
        )
        body = response.json()["data"]
        assert body["count"] == 1
        assert body["results"][0]["user"] == employee_user.get_full_name() or employee_user.username

    def test_lead_type_filter(self, authenticated_admin, company, admin_user):
        fresh = Client.objects.create(
            name="Fresh Lead",
            company=company,
            priority="low",
            type="fresh",
        )
        cold = Client.objects.create(
            name="Cold Lead",
            company=company,
            priority="low",
            type="cold",
        )
        ClientTask.objects.create(client=fresh, notes="fresh", created_by=admin_user)
        ClientTask.objects.create(client=cold, notes="cold", created_by=admin_user)

        response = authenticated_admin.get("/api/v1/activities/?lead_type=cold")
        body = response.json()["data"]
        assert body["count"] == 1
        assert body["results"][0]["lead"] == "Cold Lead"
