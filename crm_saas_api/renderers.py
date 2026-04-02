"""
Global JSON envelope for DRF: every successful JSON body becomes
    { "success": true, "data": <payload> }
unless the payload already includes a boolean "success" (from success_response,
error_response, or the exception handler).

OpenAPI / Swagger / ReDoc views are left unchanged so schema and docs keep working.
"""
from rest_framework import status
from rest_framework.renderers import JSONRenderer

_SKIP_VIEW_NAMES = frozenset(
    {
        "SpectacularAPIView",
        "SpectacularSwaggerView",
        "SpectacularRedocView",
    }
)

_ERROR_CODE_BY_STATUS = {
    400: "bad_request",
    401: "authentication_failed",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "throttled",
    500: "server_error",
    502: "bad_gateway",
    503: "service_unavailable",
}


class EnvelopeJSONRenderer(JSONRenderer):
    """
    Wraps 2xx JSON bodies; normalises remaining error bodies that are not already enveloped.
    """

    def render(self, data, accepted_media_type, renderer_context):
        if renderer_context is None:
            return super().render(data, accepted_media_type, renderer_context)

        view = renderer_context.get("view")
        if view is not None and view.__class__.__name__ in _SKIP_VIEW_NAMES:
            return super().render(data, accepted_media_type, renderer_context)

        response = renderer_context.get("response")
        if response is None:
            return super().render(data, accepted_media_type, renderer_context)

        status_code = response.status_code

        if isinstance(data, dict) and "success" in data and isinstance(data.get("success"), bool):
            return super().render(data, accepted_media_type, renderer_context)

        if status_code == status.HTTP_204_NO_CONTENT:
            return b""

        if status_code >= 400:
            wrapped = self._wrap_error(data, status_code)
            return super().render(wrapped, accepted_media_type, renderer_context)

        out = {"success": True}
        if data is not None:
            out["data"] = data
        return super().render(out, accepted_media_type, renderer_context)

    def _wrap_error(self, data, status_code):
        code = _ERROR_CODE_BY_STATUS.get(status_code, "error")

        if isinstance(data, dict):
            if "detail" in data and len(data) == 1:
                return {
                    "success": False,
                    "error": {
                        "code": code,
                        "message": str(data["detail"]),
                    },
                }
            if status_code == 400 and any(
                isinstance(v, (list, dict)) for v in data.values()
            ):
                return {
                    "success": False,
                    "error": {
                        "code": "validation_error",
                        "message": "Validation failed.",
                        "details": data,
                    },
                }
            if "error" in data or "message" in data:
                msg = data.get("error", data.get("message", "Error"))
                err = {
                    "success": False,
                    "error": {
                        "code": str(data.get("code", code)),
                        "message": str(msg),
                    },
                }
                extra = {
                    k: v
                    for k, v in data.items()
                    if k not in ("error", "message", "code", "detail")
                }
                if extra:
                    err["error"]["details"] = extra
                return err

        if isinstance(data, list) and data:
            return {
                "success": False,
                "error": {"code": code, "message": str(data[0])},
            }

        return {
            "success": False,
            "error": {
                "code": code,
                "message": str(data) if data is not None else "Error",
            },
        }
