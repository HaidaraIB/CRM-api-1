"""
Custom middleware for API security:
- DisableCSRFForAPI: Skips CSRF checks for JWT-authenticated API endpoints.
- APIKeyValidationMiddleware: Requires X-API-Key header for non-public API routes.
"""
import logging
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.conf import settings

logger = logging.getLogger(__name__)


def _api_path_for_public_match(path: str) -> str:
    """
    PUBLIC_ENDPOINTS are written for /api/...; canonical routes also live under /api/v1/...
    Normalize so payment callbacks, OAuth, and public lists work with either prefix.
    """
    if path.startswith("/api/v1/"):
        return "/api/" + path[len("/api/v1/") :]
    return path


class DisableCSRFForAPI(MiddlewareMixin):
    """Disable CSRF for API endpoints (they use JWT, not cookies)."""

    def process_request(self, request):
        if request.path.startswith("/api/"):
            setattr(request, "_dont_enforce_csrf_checks", True)
        return None


class APIKeyValidationMiddleware(MiddlewareMixin):
    """Validate X-API-Key header for all non-public API requests."""

    PUBLIC_ENDPOINTS = [
        "/api/docs/",
        "/api/schema/",
        "/api/redoc/",
        "/api-auth/",
        "/api/public/",
        "/api/payments/paytabs-return/",
        "/api/payments/zaincash-return/",
        "/api/payments/stripe-return/",
        "/api/payments/qicard-return/",
        "/api/payments/qicard-webhook/",
        "/api/payments/fib-callback/",
        "/api/integrations/accounts/oauth/callback/",
        "/api/integrations/webhooks/",
    ]

    def process_request(self, request):
        if not request.path.startswith("/api/"):
            return None

        match_path = _api_path_for_public_match(request.path)
        if any(match_path.startswith(ep) for ep in self.PUBLIC_ENDPOINTS):
            return None

        api_key = (
            request.META.get("HTTP_X_API_KEY", "")
            or request.headers.get("X-API-Key", "")
        )

        allowed_keys = [
            k for k in [
                getattr(settings, "API_KEY_MOBILE", ""),
                getattr(settings, "API_KEY_WEB", ""),
                getattr(settings, "API_KEY_ADMIN", ""),
            ] if k
        ]

        if not allowed_keys:
            logger.warning("No API keys configured. Skipping validation.")
            return None

        if not api_key:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "missing_api_key",
                        "message": "API key is required. Provide X-API-Key header.",
                    },
                },
                status=401,
            )

        if api_key not in allowed_keys:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "invalid_api_key",
                        "message": "Invalid API key. Access denied.",
                    },
                },
                status=401,
            )

        return None
