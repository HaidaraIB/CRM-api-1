"""
Unified response helpers to maintain consistent API envelope format.

Success:
    {
        "success": true,
        "message": "...",        # optional
        "data": { ... }          # optional
    }

Error:
    {
        "success": false,
        "error": {
            "code": "...",
            "message": "...",
            "details": { ... }   # optional
        }
    }
"""
from rest_framework.response import Response
from rest_framework import status as http_status


def success_response(
    data=None,
    message=None,
    status_code=http_status.HTTP_200_OK,
    headers=None,
):
    """Return a consistently shaped success response."""
    payload = {"success": True}
    if message:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    return Response(payload, status=status_code, headers=headers)


def error_response(
    message,
    code="error",
    details=None,
    status_code=http_status.HTTP_400_BAD_REQUEST,
):
    """Return a consistently shaped error response."""
    payload = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details is not None:
        payload["error"]["details"] = details
    return Response(payload, status=status_code)


def validation_error_response(
    errors,
    status_code=http_status.HTTP_400_BAD_REQUEST,
):
    """DRF serializer / field validation errors in the unified envelope."""
    return error_response(
        "Validation failed.",
        code="validation_error",
        details=errors,
        status_code=status_code,
    )
