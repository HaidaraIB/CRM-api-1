"""
Custom middleware for API security:
- MaintenanceMiddleware: Blocks API when platform maintenance mode is enabled.
- DisableCSRFForAPI: Skips CSRF checks for JWT-authenticated API endpoints.
- APIKeyValidationMiddleware: Requires X-API-Key header for non-public API routes.
- BillingScopeMiddleware: Confines billing-scoped tokens to checkout endpoints.
"""
import logging
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.conf import settings

from accounts.billing_access import (
    BILLING_SCOPE,
    bearer_token_from_request,
    scope_from_raw_token,
)
from settings.maintenance_policy import (
    get_maintenance_policy,
    request_language_from_meta,
    resolve_maintenance_message,
)

logger = logging.getLogger(__name__)

# Paths that stay reachable during maintenance (same set as API key public routes).
MAINTENANCE_WHITELIST_PREFIXES = [
    "/api/docs/",
    "/api/schema/",
    "/api/redoc/",
    "/api-auth/",
    "/api/public/",
    "/api/payments/paytabs-return/",
    "/api/payments/paytabs-callback/",
    "/api/payments/zaincash-return/",
    "/api/payments/stripe-return/",
    "/api/payments/stripe-webhook/",
    "/api/payments/qicard-return/",
    "/api/payments/qicard-webhook/",
    "/api/payments/fib-callback/",
    "/api/payments/alqaseh-return/",
    "/api/payments/alqaseh-webhook/",
    "/api/integrations/accounts/oauth/callback/",
    "/api/integrations/webhooks/",
    "/api/integrations/pbx/connector/",
    "/api/integrations/leads/inbound/",
    "/api/integrations/leads/mujeb/",
]


def _api_path_for_public_match(path: str) -> str:
    """
    PUBLIC_ENDPOINTS are written for /api/...; canonical routes also live under /api/v1/...
    Normalize so payment callbacks, OAuth, and public lists work with either prefix.
    """
    if path.startswith("/api/v1/"):
        return "/api/" + path[len("/api/v1/") :]
    return path


class MaintenanceMiddleware(MiddlewareMixin):
    """Return 503 for all non-whitelisted API routes when maintenance mode is on."""

    def process_request(self, request):
        if not request.path.startswith("/api/"):
            return None

        match_path = _api_path_for_public_match(request.path)
        if any(match_path.startswith(ep) for ep in MAINTENANCE_WHITELIST_PREFIXES):
            return None

        policy = get_maintenance_policy()
        if not policy.get("enabled"):
            return None

        lang = request_language_from_meta(getattr(request, "META", {}) or {})
        message = resolve_maintenance_message(
            str(policy.get("message") or ""),
            lang=lang,
        )
        return JsonResponse(
            {
                "success": False,
                "error": {
                    "code": "maintenance_mode",
                    "message": message,
                },
            },
            status=503,
        )


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
        "/api/payments/paytabs-callback/",
        "/api/payments/zaincash-return/",
        "/api/payments/stripe-return/",
        "/api/payments/stripe-webhook/",
        "/api/payments/qicard-return/",
        "/api/payments/qicard-webhook/",
        "/api/payments/fib-callback/",
        "/api/payments/alqaseh-return/",
        "/api/payments/alqaseh-webhook/",
        "/api/integrations/accounts/oauth/callback/",
        "/api/integrations/webhooks/",
        "/api/integrations/pbx/connector/",
        "/api/integrations/leads/inbound/",
        "/api/integrations/leads/mujeb/",
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


class BillingScopeMiddleware(MiddlewareMixin):
    """
    Confine billing-scoped access tokens to the endpoints needed to pay.

    These tokens go to owners whose subscription has lapsed (see
    accounts.billing_access), who otherwise cannot authenticate at all. They
    must not double as a way back into the CRM the tenant has stopped paying
    for.

    This is middleware rather than a permission class on purpose:
    HasActiveSubscription is opted into per view across dozens of modules, so
    any view that forgot it would be reachable. An allowlist here is
    fail-closed — a new endpoint stays denied to these tokens until someone
    deliberately adds it below.
    """

    ALLOWED_PREFIXES = [
        "/api/public/",
        "/api/payment-status/",
        "/api/payments/create-paytabs-session/",
        "/api/payments/create-zaincash-session/",
        "/api/payments/create-stripe-session/",
        "/api/payments/create-qicard-session/",
        "/api/payments/create-fib-session/",
        "/api/payments/create-alqaseh-session/",
        # Read-only pricing preview: the login screen offers "change plan" as an
        # alternative to renewing, and that page prices the change before checkout.
        "/api/subscriptions/preview-change/",
    ]

    def process_request(self, request):
        if not request.path.startswith("/api/"):
            return None

        raw_token = bearer_token_from_request(request)
        if scope_from_raw_token(raw_token) != BILLING_SCOPE:
            return None

        match_path = _api_path_for_public_match(request.path)
        if any(match_path.startswith(ep) for ep in self.ALLOWED_PREFIXES):
            return None

        logger.info("Billing-scoped token denied for %s %s", request.method, request.path)
        # Deliberately not the `subscription_inactive` code: clients treat that
        # one as "session is dead, wipe it and bounce to login", which would
        # throw away the very token the user needs to finish paying.
        return JsonResponse(
            {
                "success": False,
                "error": {
                    "code": "billing_scope_only",
                    "message": "This session can only be used to complete payment.",
                },
            },
            status=403,
        )
