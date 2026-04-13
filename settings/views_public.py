"""Unauthenticated public API views for settings (mobile clients)."""

from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from crm_saas_api.responses import success_response

from .models import SystemSettings


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
