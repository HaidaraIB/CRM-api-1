from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import CanViewCompanyReports, HasActiveSubscription
from crm.models import Client
from crm_saas_api.responses import success_response

from .params import parse_params
from .selectors import base, calls, deals, employees, journey, lead_list, leads, overview, response, sources

SECTIONS = {
    "overview": overview.build,
    "leads": leads.build,
    "status-journey": journey.build,
    "response-time": response.build,
    "employees": employees.build,
    "sources": sources.build,
    "calls": calls.build,
    "deals": deals.build,
}


class ReportSectionView(APIView):
    """GET /reports/v2/<section>/ — every section shares one parameter contract (see reports.params)."""

    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request, section):
        builder = SECTIONS[section]
        p = parse_params(request)
        payload = builder(p)
        payload["meta"] = {
            "section": section,
            "start": p.start.isoformat(),
            "end": p.end.isoformat(),
            "timezone": str(p.tz),
            "granularity": p.granularity,
            "data_quality": base.data_quality(p),
        }
        return success_response(payload)


class ReportLeadListView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request):
        p = parse_params(request)
        return success_response(lead_list.build(p, request))


class ReportLeadJourneyView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription, CanViewCompanyReports]

    def get(self, request, client_id):
        company = request.user.company
        client = get_object_or_404(Client, pk=client_id, company=company)
        return success_response(journey.lead_journey(company, client))
