"""GET /api/v1/validation/catalog/"""

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response

from crm_saas_api.responses import error_response
from validation.catalog import catalog_document
from validation.registry import all_forms, get_form, public_forms


def tenant_context_flags(request) -> dict:
    flags = {"hasCompany": False, "specialization": "", "requireCompany": False}
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return flags
    company = getattr(user, "company", None)
    if company is not None:
        flags["hasCompany"] = True
        flags["specialization"] = getattr(company, "specialization", "") or ""
    else:
        flags["requireCompany"] = True
    is_super = bool(getattr(user, "is_superuser", False))
    if not is_super and hasattr(user, "is_super_admin"):
        try:
            is_super = bool(user.is_super_admin())
        except Exception:
            is_super = False
    flags["isSuperAdmin"] = is_super
    return flags


class ValidationCatalogView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raw = (request.query_params.get("forms") or "").strip()
        requested = [item.strip() for item in raw.split(",") if item.strip()] if raw else None
        try:
            if requested is None:
                selected = all_forms() if request.user and request.user.is_authenticated else public_forms()
            else:
                missing = [form_id for form_id in requested if form_id not in all_forms()]
                if missing:
                    return error_response(
                        "Unknown form.",
                        code="not_found",
                        details={"forms": missing},
                        status_code=404,
                    )
                needs_auth = any(not get_form(form_id).public for form_id in requested)
                if needs_auth and not (request.user and request.user.is_authenticated):
                    return error_response(
                        "Authentication required.",
                        code="authentication_failed",
                        status_code=401,
                    )
                selected = {form_id: get_form(form_id) for form_id in requested}
        except KeyError:
            return error_response("Unknown form.", code="not_found", status_code=404)

        document = catalog_document(selected)
        document["context"] = tenant_context_flags(request)
        etag = f'"{document["version"]}"'
        if request.headers.get("If-None-Match") == etag:
            response = HttpResponse(status=304)
            response["ETag"] = etag
            return response
        response = Response(document)
        response["ETag"] = etag
        response["Cache-Control"] = "private, max-age=300"
        return response
