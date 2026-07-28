"""
Unified exception handler for DRF.
Normalises every error response to a consistent envelope format:

    {
        "success": false,
        "error": {
            "code": "<error_code>",
            "message": "<human-readable summary>",
            "details": { ... }       # optional field-level errors
            "hint": "...",           # optional (login verification, etc.)
            "actions": [ ... ],      # optional
            "change_credentials_note": "..."  # optional
        }
    }
"""
import logging
from rest_framework.views import exception_handler
from rest_framework import status as http_status
from django.http import Http404
from django.core.exceptions import PermissionDenied

from crm_saas_api.responses import error_response
from accounts.exceptions import LoginVerificationRequired

logger = logging.getLogger(__name__)

STATUS_CODE_MAP = {
    400: "bad_request",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    429: "throttled",
    500: "server_error",
}

# Keys that carry a business code / metadata, not field validation errors
_META_KEYS = frozenset(
    {"error", "message", "detail", "code", "error_key", "subscriptionId", "subscription_id"}
)


def _unwrap_value(value):
    """Turn DRF ErrorDetail lists into a plain string/value."""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return _unwrap_value(value[0])
    return value


def _unwrap_str(value) -> str:
    unwrapped = _unwrap_value(value)
    if unwrapped is None:
        return ""
    return str(unwrapped)


def _looks_like_business_code(value: str) -> bool:
    """True for identifiers like SUBSCRIPTION_INACTIVE; false for field error sentences."""
    if not value or len(value) > 64 or " " in value:
        return False
    return value.replace("_", "").isalnum()


def custom_exception_handler(exc, context):
    """
    Wrap DRF's default handler output in a unified envelope.
    Returns None for non-DRF exceptions so Django's 500 handler takes over.
    """
    if isinstance(exc, LoginVerificationRequired):
        return error_response(
            str(exc.detail),
            code=exc.business_code,
            status_code=exc.status_code,
            verify_email_url=exc.verify_email_url or None,
            verify_phone_url=exc.verify_phone_url or None,
        )

    response = exception_handler(exc, context)

    if response is None:
        return None

    code = STATUS_CODE_MAP.get(response.status_code, "error")
    details = None
    message = ""

    data = response.data

    if isinstance(data, list):
        message = _unwrap_str(data) or "An error occurred."
    elif isinstance(data, dict):
        if "detail" in data:
            message = _unwrap_str(data["detail"])
        elif "error" in data:
            message = _unwrap_str(data["error"])
        elif "message" in data:
            message = _unwrap_str(data["message"])
        else:
            message = "Validation failed."
            details = data

        # Prefer explicit business code from payload (error_key or code).
        # Skip non-identifier "code" values (e.g. OTP field: "Either code or token...").
        for key in ("error_key", "code"):
            if key not in data:
                continue
            business = _unwrap_str(data[key])
            if business and (key == "error_key" or _looks_like_business_code(business)):
                code = business
                break

        # Preserve subscription id for inactive-subscription login errors
        sid = None
        if "subscriptionId" in data:
            sid = _unwrap_value(data["subscriptionId"])
        elif "subscription_id" in data:
            sid = _unwrap_value(data["subscription_id"])
        if sid is not None and sid != "":
            plain_sid: object = str(sid)
            try:
                plain_sid = int(plain_sid)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass
            if details is None:
                details = {}
            if isinstance(details, dict):
                details = {**details, "subscriptionId": plain_sid}

        # If we took message from error/detail but other non-meta keys remain,
        # keep them as details (field errors) when details is still empty.
        if details is None:
            leftover = {k: v for k, v in data.items() if k not in _META_KEYS}
            if leftover:
                details = leftover
    else:
        message = str(data)

    response.data = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
        },
    }

    if details:
        response.data["error"]["details"] = details

    return response
