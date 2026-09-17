from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import (
    CanAccessClient,
    DenyCallCenterNonLeadAPI,
    DenyDataEntryNonLeadAPI,
    HasActiveSubscription,
)
from crm.activities import build_activities_list, parse_activity_filters_from_request
from crm_saas_api.pagination import FlexiblePageNumberPagination


class ActivitiesListView(APIView):
    """GET /api/v1/activities/ — paginated merged client tasks + client calls."""

    permission_classes = [
        IsAuthenticated,
        HasActiveSubscription,
        DenyDataEntryNonLeadAPI,
        DenyCallCenterNonLeadAPI,
        CanAccessClient,
    ]
    pagination_class = FlexiblePageNumberPagination

    def get(self, request):
        filters = parse_activity_filters_from_request(request)
        rows = build_activities_list(request.user, filters)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(page)
