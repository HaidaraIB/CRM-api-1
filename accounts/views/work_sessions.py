"""
Work-session endpoints: measured CRM usage time.

Deliberately separate from ``UserViewSet.presence_heartbeat``. That action is also
called by the mobile app on every foreground resume and has no company opt-in gate,
no role gate, and no impersonation guard — extending it would have started writing
hours for every existing tenant. The ping here refreshes presence as a side effect
(same row, same UPDATE), so clients that run this loop can skip the legacy heartbeat
and total request volume stays flat.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import HasActiveSubscription, is_impersonating
from accounts.work_tracking import (
    PING_INTERVAL_SECONDS,
    company_tracking_config,
    company_user_totals,
    credit_work_time,
    today_summary_for,
    user_is_work_tracked,
)
from crm_saas_api.responses import error_response, success_response
from crm_saas_api.utils import clean_int_query_param


def _inert_payload(user, reason):
    """A 200 that tells the client to stand down, without writing anything."""
    _, idle_seconds = company_tracking_config(getattr(user, "company", None))
    return {
        "tracking_enabled": False,
        "reason": reason,
        "ping_interval_seconds": PING_INTERVAL_SECONDS,
        "idle_timeout_minutes": idle_seconds // 60,
        "work_date": None,
        "today_seconds": 0,
        "credited_seconds": 0,
    }


class WorkSessionPingView(APIView):
    """
    POST /api/v1/work-sessions/ping/

    Body: ``{"source": "web" | "mobile"}`` — the source only. A duration is never
    accepted: elapsed time is derived from the server clock and the server-stored
    cursor, which is what bounds how much a forged request can gain.
    """

    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def post(self, request):
        user = request.user

        # A super admin supporting a tenant must not accrue hours for that user.
        if is_impersonating(request):
            return success_response(data=_inert_payload(user, "impersonation"))

        if not user_is_work_tracked(user):
            enabled, _ = company_tracking_config(getattr(user, "company", None))
            reason = "tracking_disabled" if not enabled else "role_not_tracked"
            return success_response(data=_inert_payload(user, reason))

        result = credit_work_time(user, source=request.data.get("source"))
        return success_response(
            data={
                "tracking_enabled": result["tracking_enabled"],
                "ping_interval_seconds": PING_INTERVAL_SECONDS,
                "idle_timeout_minutes": result["idle_timeout_minutes"],
                "work_date": result["work_date"],
                "today_seconds": result["today_seconds"],
                "credited_seconds": result["credited_seconds"],
                "server_time": user.work_last_ping_at,
            }
        )


class WorkSessionTodayView(APIView):
    """
    GET /api/v1/work-sessions/today/

    Feeds the employee-facing "today" indicator. Scoped to the requesting user only;
    managers read other people's totals through the Employees Report instead.
    """

    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def get(self, request):
        return success_response(data=today_summary_for(request.user))


class WorkSessionSummaryView(APIView):
    """
    GET /api/v1/work-sessions/summary/?days=7

    Measured hours for every tracked user in the company, as one grouped aggregate.

    Exists so the Employees page can label a whole page of cards without an N+1, and
    is separate from the users list because that endpoint is also hit by assignee
    pickers and name lookups, which have no use for an extra aggregate join.

    Gated to the same audience that can already see presence on those cards: company
    admins, or supervisors with `manage_users`. Employees read only their own total,
    via `work-sessions/today/`.
    """

    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def get(self, request):
        user = request.user
        company = getattr(user, "company", None)
        if not company:
            return error_response(
                "No company on this account.",
                code="no_company",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        allowed = user.is_admin() or user.supervisor_has_permission("manage_users")
        if not allowed:
            return error_response(
                "You do not have permission to view team working hours.",
                code="permission_denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        enabled, idle_seconds = company_tracking_config(company)
        if not enabled:
            return success_response(
                data={
                    "tracking_enabled": False,
                    "work_date": None,
                    "days": 0,
                    "idle_timeout_minutes": idle_seconds // 60,
                    "users": [],
                }
            )

        days = clean_int_query_param(request, "days") or 7
        summary = company_user_totals(company, days=days)
        return success_response(
            data={
                "tracking_enabled": True,
                "work_date": summary["work_date"],
                "days": summary["days"],
                "idle_timeout_minutes": idle_seconds // 60,
                "users": [
                    {
                        "user_id": user_id,
                        "today_seconds": totals["today_seconds"],
                        "range_seconds": totals["range_seconds"],
                    }
                    for user_id, totals in sorted(summary["totals"].items())
                ],
            }
        )
