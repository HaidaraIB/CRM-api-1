"""
Tests for OpenAI / AI insights integration (v1 API).
"""
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from conftest import api_body
from integrations.models import AIInsightStatus, ClientAIInsight, OpenAISettings
from crm.models import Client, ClientTask


@pytest.mark.django_db
class TestOpenAISettingsAPI:
    def test_get_settings_empty(self, authenticated_admin, company):
        response = authenticated_admin.get("/api/v1/integrations/openai/settings/")
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["is_enabled"] is False
        assert data["model"] == "gpt-4o-mini"

    def test_put_settings_masks_key(self, authenticated_admin, company):
        payload = {
            "api_key": "sk-test-secret-key-12345",
            "is_enabled": True,
            "model": "gpt-4o-mini",
            "max_leads_per_run": 10,
        }
        response = authenticated_admin.put(
            "/api/v1/integrations/openai/settings/",
            payload,
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = api_body(response)
        assert data["is_enabled"] is True
        assert data.get("api_key_masked")
        assert "sk-" in data["api_key_masked"]
        settings = OpenAISettings.objects.get(company=company)
        assert settings.get_api_key() == "sk-test-secret-key-12345"

    def test_legacy_unversioned_path(self, authenticated_admin):
        response = authenticated_admin.get("/api/integrations/openai/settings/")
        assert response.status_code == status.HTTP_200_OK

    def test_post_test_not_configured(self, authenticated_admin):
        response = authenticated_admin.post(
            "/api/v1/integrations/openai/settings/test/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "openai_not_configured"

    @patch("integrations.views.openai_ai.test_openai_connection", return_value=(True, ""))
    def test_post_test_with_draft_api_key(self, _mock_test, authenticated_admin):
        response = authenticated_admin.post(
            "/api/v1/integrations/openai/settings/test/",
            {"api_key": "sk-draft-test", "model": "gpt-4o-mini"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert api_body(response)["ok"] is True


@pytest.mark.django_db
class TestAIInsightApprove:
    def test_approve_creates_client_task(self, authenticated_admin, company, admin_user):
        client = Client.objects.create(
            name="AI Lead",
            company=company,
            priority="high",
            type="fresh",
            patient_file_number=1,
        )
        reminder = timezone.now() + timezone.timedelta(days=1)
        insight = ClientAIInsight.objects.create(
            company=company,
            client=client,
            ai_score=85,
            priority_level="high",
            summary="Needs urgent follow-up",
            suggested_reminder_date=reminder,
            suggested_task_notes="Call about pricing",
            status=AIInsightStatus.PENDING,
        )
        response = authenticated_admin.post(
            f"/api/v1/integrations/ai-insights/{insight.id}/approve/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        body = api_body(response)
        assert body["client_task_id"]
        insight.refresh_from_db()
        assert insight.status == AIInsightStatus.APPROVED
        assert insight.created_client_task_id == body["client_task_id"]
        task = ClientTask.objects.get(id=body["client_task_id"])
        assert task.client_id == client.id
        assert task.reminder_date is not None
        assert "AI" in (task.notes or "")

    def test_dismiss_insight(self, authenticated_admin, company):
        client = Client.objects.create(
            name="Dismiss Lead",
            company=company,
            priority="medium",
            type="fresh",
            patient_file_number=2,
        )
        insight = ClientAIInsight.objects.create(
            company=company,
            client=client,
            ai_score=50,
            summary="Low priority",
            status=AIInsightStatus.PENDING,
        )
        response = authenticated_admin.post(
            f"/api/v1/integrations/ai-insights/{insight.id}/dismiss/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        insight.refresh_from_db()
        assert insight.status == AIInsightStatus.DISMISSED
        assert ClientTask.objects.filter(client=client).count() == 0


@pytest.mark.django_db
class TestAIAnalysisService:
    @patch("integrations.services.ai_lead_analysis._call_openai")
    def test_run_company_analysis_creates_pending(
        self, mock_openai, company, admin_user
    ):
        from integrations.services.ai_lead_analysis import run_company_analysis

        client = Client.objects.create(
            name="Analyze Me",
            company=company,
            priority="high",
            type="fresh",
            notes="Interested in premium package",
            patient_file_number=3,
        )
        settings = OpenAISettings.objects.create(company=company, is_enabled=True)
        settings.set_api_key("sk-fake")
        settings.save()

        mock_openai.return_value = (
            [
                {
                    "client_id": client.id,
                    "ai_score": 90,
                    "priority_level": "high",
                    "summary_en": "Hot lead from notes",
                    "summary_ar": "عميل مهم من الملاحظات",
                    "suggested_reminder_date": timezone.now().isoformat(),
                    "suggested_task_notes_en": "Follow up tomorrow",
                    "suggested_task_notes_ar": "متابعة غداً",
                }
            ],
            100,
        )

        result = run_company_analysis(company)
        assert result.get("created", 0) >= 1
        insight = ClientAIInsight.objects.filter(
            client=client, status=AIInsightStatus.PENDING
        ).first()
        assert insight is not None
        assert insight.ai_score == 90
        assert insight.summary_en == "Hot lead from notes"
        assert insight.summary_ar == "عميل مهم من الملاحظات"
        assert insight.suggested_task_notes_ar == "متابعة غداً"
