from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import CanViewCompanyReports, HasActiveSubscription
from crm.report_metrics import build_call_report, build_employee_or_team_rows, build_marketing_rows
from crm_saas_api.responses import success_response
from crm_saas_api.utils import clean_int_query_param


def _parse_report_filters(request):
    return {
        "from_date": (request.query_params.get("from") or "").strip() or None,
        "to_date": (request.query_params.get("to") or "").strip() or None,
        "lead_type": (request.query_params.get("lead_type") or "").strip() or None,
        "user_id": clean_int_query_param(request, "user_id"),
        "campaign_id": clean_int_query_param(request, "campaign_id"),
    }


class EmployeeReportView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request):
        company = request.user.company
        filters = _parse_report_filters(request)
        rows, summary = build_employee_or_team_rows(
            company,
            from_date=filters["from_date"],
            to_date=filters["to_date"],
            lead_type=filters["lead_type"],
            user_id=filters["user_id"],
        )
        return success_response({"rows": rows, "summary": summary})


class TeamsReportView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request):
        company = request.user.company
        filters = _parse_report_filters(request)
        rows, summary = build_employee_or_team_rows(
            company,
            from_date=filters["from_date"],
            to_date=filters["to_date"],
            lead_type=filters["lead_type"],
            user_id=filters["user_id"],
        )
        return success_response({"rows": rows, "summary": summary})


class MarketingReportView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request):
        company = request.user.company
        filters = _parse_report_filters(request)
        rows, summary = build_marketing_rows(
            company,
            from_date=filters["from_date"],
            to_date=filters["to_date"],
            lead_type=filters["lead_type"],
            campaign_id=filters["campaign_id"],
        )
        return success_response({"rows": rows, "summary": summary})


class CallReportView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request):
        company = request.user.company
        filters = _parse_report_filters(request)
        payload = build_call_report(
            company,
            from_date=filters["from_date"],
            to_date=filters["to_date"],
            user_id=filters["user_id"],
        )
        return success_response(payload)
