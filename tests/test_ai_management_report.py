from unittest.mock import patch

import pytest

from integrations.services.ai_management_report import (
    build_employee_daily_stats,
    build_hot_leads_snapshot,
    generate_management_report,
)
from integrations.models import OpenAISettings


@pytest.mark.django_db
class TestAIManagementReport:
    def test_build_hot_leads_includes_hot_type(self, company, admin_user):
        from crm.models import Client

        client = Client.objects.create(
            name="Hot Client",
            company=company,
            priority="medium",
            type="hot",
            patient_file_number=99,
        )
        items = build_hot_leads_snapshot(company)
        ids = [i["client_id"] for i in items]
        assert client.id in ids

    @patch("integrations.services.ai_management_report._call_openai_management_report")
    def test_generate_management_report(self, mock_ai, company, admin_user):
        settings = OpenAISettings.objects.create(company=company, is_enabled=True)
        settings.set_api_key("sk-fake")
        settings.save()
        mock_ai.return_value = (
            {
                "employee_performance_en": "Team active today.",
                "employee_performance_ar": "الفريق نشط اليوم.",
                "hot_leads_summary_en": "Follow hot leads.",
                "hot_leads_summary_ar": "تابع العملاء الساخنين.",
            },
            50,
        )
        result = generate_management_report(company)
        assert result.get("has_ai_summary") is True
        assert "employee_performance_en" in result or result.get("employee_performance_en") == ""
