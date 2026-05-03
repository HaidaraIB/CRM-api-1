"""
Unified exception handler for DRF.
Normalises every error response to a consistent envelope format:

    {
        "success": false,
        "error": {
            "code": "<error_code>",
            "message": "<human-readable summary>",
            "details": { ... }       # optional field-level errors
        }
    }
"""
import logging
from rest_framework.views import exception_handler
from rest_framework import status as http_status
from django.http import Http404
from django.core.exceptions import PermissionDenied

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


def custom_exception_handler(exc, context):
    """
    Wrap DRF's default handler output in a unified envelope.
    Returns None for non-DRF exceptions so Django's 500 handler takes over.
    """
    response = exception_handler(exc, context)

    if response is None:
        return None

    code = STATUS_CODE_MAP.get(response.status_code, "error")
    details = None
    message = ""

    data = response.data

    if isinstance(data, list):
        message = data[0] if data else "An error occurred."
    elif isinstance(data, dict):
        if "detail" in data:
            message = str(data["detail"])
        elif "error" in data:
            message = str(data["error"])
        elif "message" in data:
            message = str(data["message"])
        else:
            message = "Validation failed."
            details = data

        if "error_key" in data:
            ek = data["error_key"]
            if isinstance(ek, (list, tuple)) and ek:
                code = str(ek[0])
            else:
                code = str(ek)
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
