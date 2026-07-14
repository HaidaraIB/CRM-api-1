"""
Tests for tenant CRM report endpoints.
"""
import pytest
from rest_framework import status

from conftest import api_body
from crm.models import Campaign, Client, ClientCall, Deal
from settings.company_defaults import seed_company_settings


@pytest.fixture
def report_company(company):
    seed_company_settings(company)
    return company


@pytest.fixture
def report_users(report_company, admin_user, employee_user):
    return {"admin": admin_user, "employee": employee_user}


@pytest.fixture
def report_campaign(report_company):
    return Campaign.objects.create(
        company=report_company,
        code="SPRING",
        name="Spring Campaign",
        budget="1000.00",
    )


@pytest.fixture
def report_data(report_company, report_users, report_campaign):
    from settings.models import LeadStatus

    employee = report_users["employee"]
    statuses = {
        row.name: row
        for row in LeadStatus.objects.filter(company=report_company, is_active=True)
    }

    lead_new = Client.objects.create(
        name="Lead New",
        company=report_company,
        assigned_to=employee,
        campaign=report_campaign,
        type="fresh",
        priority="low",
        status=statuses["New lead"],
    )
    lead_follow = Client.objects.create(
        name="Lead Follow",
        company=report_company,
        assigned_to=employee,
        campaign=report_campaign,
        type="fresh",
        priority="low",
        status=statuses["Follow up"],
    )
    lead_other_campaign = Client.objects.create(
        name="Lead Other",
        company=report_company,
        assigned_to=employee,
        type="fresh",
        priority="low",
        status=statuses["Qualified"],
    )

    Deal.objects.create(
        client=lead_follow,
        company=report_company,
        employee=employee,
        stage="won",
    )
    Deal.objects.create(
        client=lead_new,
        company=report_company,
        employee=employee,
        stage="in_progress",
    )

    ClientCall.objects.create(client=lead_new, created_by=employee)
    ClientCall.objects.create(client=lead_follow, created_by=employee)

    return {
        "employee": employee,
        "campaign": report_campaign,
        "lead_new": lead_new,
        "lead_follow": lead_follow,
        "lead_other_campaign": lead_other_campaign,
    }


@pytest.mark.django_db
class TestEmployeeReportAPI:
    def test_employee_report_returns_non_zero_counts(
        self, authenticated_admin, report_data
    ):
        response = authenticated_admin.get("/api/v1/reports/employees/")
        assert response.status_code == status.HTTP_200_OK

        payload = api_body(response)
        assert payload["summary"]["total_calls"] == 2
        assert payload["summary"]["employee_count"] >= 1

        employee_row = next(
            row for row in payload["rows"] if row["id"] == report_data["employee"].id
        )
        assert employee_row["total_leads"] == 3
        assert employee_row["following"] == 1
        assert employee_row["total_deals"] == 2
        assert employee_row["won_deals"] == 1
        assert employee_row["total_calls"] == 2

    def test_employee_report_denied_for_employee(
        self, authenticated_employee, report_data
    ):
        response = authenticated_employee.get("/api/v1/reports/employees/")
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestTeamsReportAPI:
    def test_teams_report_filters_by_user(self, authenticated_admin, report_data):
        employee_id = report_data["employee"].id
        response = authenticated_admin.get(
            f"/api/v1/reports/teams/?user_id={employee_id}"
        )
        assert response.status_code == status.HTTP_200_OK

        payload = api_body(response)
        assert len(payload["rows"]) == 1
        assert payload["rows"][0]["id"] == employee_id
        assert payload["rows"][0]["total_leads"] == 3
        assert payload["summary"]["total_leads"] == 3


@pytest.mark.django_db
class TestMarketingReportAPI:
    def test_marketing_report_groups_by_campaign(
        self, authenticated_admin, report_data, report_campaign
    ):
        response = authenticated_admin.get("/api/v1/reports/marketing/")
        assert response.status_code == status.HTTP_200_OK

        payload = api_body(response)
        campaign_row = next(
            row for row in payload["rows"] if row["id"] == report_campaign.id
        )
        assert campaign_row["total_leads"] == 2
        assert campaign_row["converted_leads"] >= 1
        assert float(campaign_row["conversion_rate"]) > 0

    def test_marketing_report_campaign_filter(
        self, authenticated_admin, report_data, report_campaign
    ):
        response = authenticated_admin.get(
            f"/api/v1/reports/marketing/?campaign_id={report_campaign.id}"
        )
        assert response.status_code == status.HTTP_200_OK

        payload = api_body(response)
        assert len(payload["rows"]) == 1
        assert payload["rows"][0]["id"] == report_campaign.id
        assert payload["rows"][0]["total_leads"] == 2


@pytest.mark.django_db
class TestCallReportAPI:
    def test_call_report_returns_crm_calls(self, authenticated_admin, report_data):
        response = authenticated_admin.get("/api/v1/reports/calls/")
        assert response.status_code == status.HTTP_200_OK

        payload = api_body(response)
        assert payload["crm"]["summary"]["total"] == 2
        assert payload["combined"]["summary"]["total"] >= 2
        assert len(payload["crm"]["by_user"]) >= 1

    def test_call_report_combined_deduplicates_linked_pbx(
        self, authenticated_admin, report_company, report_data
    ):
        from django.utils import timezone
        from integrations.models import PbxCallDisposition, PbxCallRecord, PbxEventType, PbxSettings

        PbxSettings.objects.create(
            company=report_company,
            webhook_token="wh-call-report-test",
            connector_api_key="conn-call-report-test",
            is_enabled=True,
        )
        pbx_record = PbxCallRecord.objects.create(
            company=report_company,
            uniqueid="test-uniq-1",
            event_type=PbxEventType.HANGUP,
            disposition=PbxCallDisposition.ANSWERED,
            billsec=120,
            started_at=timezone.now(),
        )
        call = ClientCall.objects.filter(client=report_data["lead_new"]).first()
        call.pbx_call_record = pbx_record
        call.source = "pbx"
        call.save(update_fields=["pbx_call_record", "source"])

        PbxCallRecord.objects.create(
            company=report_company,
            uniqueid="test-uniq-2",
            event_type=PbxEventType.HANGUP,
            disposition=PbxCallDisposition.NO_ANSWER,
            billsec=0,
            started_at=timezone.now(),
        )

        response = authenticated_admin.get("/api/v1/reports/calls/")
        payload = api_body(response)

        assert payload["crm"]["summary"]["pbx_linked"] == 1
        assert payload["pbx"]["enabled"] is True
        assert payload["pbx"]["summary"]["total"] == 2
        assert payload["combined"]["summary"]["pbx_cdr_unlinked"] == 1
        assert payload["combined"]["summary"]["total"] == 3

    def test_call_report_denied_for_employee(self, authenticated_employee, report_data):
        response = authenticated_employee.get("/api/v1/reports/calls/")
        assert response.status_code == status.HTTP_403_FORBIDDEN
