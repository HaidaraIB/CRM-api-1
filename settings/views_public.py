"""Unauthenticated public API views for settings (mobile clients)."""

from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from crm_saas_api.responses import success_response

from .maintenance_policy import (
    get_maintenance_policy,
    request_language_from_meta,
    resolve_maintenance_message,
)
from .models import SystemSettings


class MaintenanceStatusPublicView(APIView):
    """
    Public maintenance status for web/mobile/admin clients.
    No authentication required; used to show maintenance UI before API calls.
    """

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        policy = get_maintenance_policy()
        lang = request_language_from_meta(getattr(request, "META", {}) or {})
        return success_response(
            data={
                "maintenance_mode": bool(policy.get("enabled")),
                "message": resolve_maintenance_message(
                    str(policy.get("message") or ""),
                    lang=lang,
                ),
            }
        )


class MobileAppVersionPublicView(APIView):
    """
    Public policy for minimum supported mobile app versions and store URLs.
    Used by crm_mobile to gate startup (fail-closed until policy is fetched).
    """

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        s = SystemSettings.get_settings()
        data = {
            "minimum_version_android": (s.mobile_minimum_version_android or "").strip(),
            "minimum_version_ios": (s.mobile_minimum_version_ios or "").strip(),
            "minimum_build_android": s.mobile_minimum_build_android,
            "minimum_build_ios": s.mobile_minimum_build_ios,
            "store_url_android": (s.mobile_store_url_android or "").strip(),
            "store_url_ios": (s.mobile_store_url_ios or "").strip(),
        }
        return success_response(data=data)
