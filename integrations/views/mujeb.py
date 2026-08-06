"""
Mujeb mini app inbound leads and config.
Reuses CompanyLeadApiKey auth; dedicated endpoint labels source=mujeb.
"""
import json
import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.decorators import rate_limit_webhook
from integrations.lead_api_keys import extract_lead_api_key_from_request, resolve_active_api_key
from integrations.models import CompanyLeadApiKey, IntegrationAccount, IntegrationPlatform
from integrations.serializers_lead_api import InboundLeadSerializer, MujebCheckLeadSerializer
from integrations.services.inbound_lead import (
    MUJEB_PLATFORM,
    check_inbound_lead_exists,
    create_inbound_lead,
    get_or_create_mujeb_account,
)
from integrations.views.lead_api import _serialize_key_row

logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or ""


def _phone_log(phone: str | None) -> str:
    """Short safe phone for logs (last 4 digits)."""
    raw = (phone or "").strip()
    if not raw:
        return "-"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 4:
        return f"...{digits[-4:]}"
    return "..."


def _mujeb_base_prefix() -> str:
    base = getattr(settings, "API_BASE_URL", "").rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/integrations/leads/mujeb"
    if base.endswith("/api"):
        return f"{base}/v1/integrations/leads/mujeb"
    if base:
        return f"{base}/api/v1/integrations/leads/mujeb"
    return "/api/v1/integrations/leads/mujeb"


def _mujeb_endpoint_url() -> str:
    return f"{_mujeb_base_prefix()}/"


def _mujeb_check_endpoint_url() -> str:
    return f"{_mujeb_base_prefix()}/check/"


def _resolve_lead_api_company(request, *, action: str):
    """Return (company, error_response). error_response is set on auth failure."""
    ip = _client_ip(request)
    raw_key = extract_lead_api_key_from_request(request)
    if not raw_key:
        logger.warning("MUJEB_%s missing API key ip=%s", action, ip)
        return None, error_response(
            "API key is required. Use Authorization: Bearer <key> or X-Lead-Api-Key.",
            code="missing_api_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    key_row = resolve_active_api_key(raw_key)
    if not key_row:
        logger.warning("MUJEB_%s invalid or inactive API key ip=%s", action, ip)
        return None, error_response(
            "Invalid or inactive API key.",
            code="invalid_api_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return key_row.company, None


def _parse_json_body(request, *, action: str):
    """Return (body_dict, error_response)."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        logger.warning("MUJEB_%s invalid JSON body ip=%s", action, _client_ip(request))
        return None, error_response(
            "Invalid JSON body.",
            code="invalid_json",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return body, None


def _handle_inbound_create_errors(exc, company_id):
    from rest_framework.exceptions import ValidationError as DRFValidationError

    if isinstance(exc, DRFValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            code = detail.get("code") or detail.get("error_key") or "validation_error"
            message = detail.get("error") or detail.get("message") or str(detail)
            status_code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
            if code == "plan_quota_max_clients_exceeded" or detail.get("error_key") == "plan_quota_max_clients_exceeded":
                status_code = status.HTTP_403_FORBIDDEN
            if detail.get("code") == "integration_disabled":
                status_code = status.HTTP_403_FORBIDDEN
            logger.warning(
                "MUJEB_CREATE rejected company_id=%s code=%s message=%s",
                company_id,
                code,
                str(message)[:200],
            )
            return error_response(str(message), code=str(code), status_code=status_code, details=detail)
        logger.warning("MUJEB_CREATE validation error company_id=%s detail=%s", company_id, detail)
        return validation_error_response(detail)
    logger.exception("MUJEB_CREATE failed company_id=%s", company_id)
    return error_response(
        "Failed to create lead.",
        code="lead_create_failed",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@extend_schema(
    tags=["Mujeb"],
    request=InboundLeadSerializer,
    responses={
        201: inline_serializer(
            name="MujebLeadCreated",
            fields={
                "success": drf_serializers.BooleanField(),
                "data": drf_serializers.DictField(),
            },
        ),
    },
    auth=[],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@rate_limit_webhook(max_requests=120, window=60)
def mujeb_inbound_lead_view(request):
    """
    POST /api/v1/integrations/leads/mujeb/
    Auth: Authorization: Bearer <company_lead_api_key> or X-Lead-Api-Key header.
    Creates a lead with source=mujeb.
    """
    ip = _client_ip(request)
    logger.info("MUJEB_CREATE_CALLED method=%s ip=%s", request.method, ip)

    company, auth_err = _resolve_lead_api_company(request, action="CREATE")
    if auth_err:
        return auth_err

    body, parse_err = _parse_json_body(request, action="CREATE")
    if parse_err:
        return parse_err

    serializer = InboundLeadSerializer(data=body, company=company)
    if not serializer.is_valid():
        logger.warning(
            "MUJEB_CREATE validation failed company_id=%s errors=%s",
            company.id,
            serializer.errors,
        )
        return validation_error_response(serializer.errors)

    payload = serializer.validated_data
    phone = (payload.get("phone") or "").strip() or None
    external_id = (payload.get("external_id") or "").strip() or None
    name = (payload.get("name") or "").strip() or None
    logger.info(
        "MUJEB_CREATE processing company_id=%s name=%s phone=%s external_id=%s",
        company.id,
        name,
        _phone_log(phone),
        external_id or "-",
    )

    account = get_or_create_mujeb_account(company)
    try:
        data, created = create_inbound_lead(
            company=company,
            account=account,
            payload=payload,
            source="mujeb",
            platform_gate=MUJEB_PLATFORM,
        )
    except Exception as exc:
        return _handle_inbound_create_errors(exc, company.id)

    if created:
        logger.info(
            "MUJEB_CREATE created client_id=%s patient_file_number=%s company_id=%s name=%s",
            data.get("client_id"),
            data.get("patient_file_number"),
            company.id,
            name,
        )
        logger.info("MUJEB_CREATE completed status=201 company_id=%s", company.id)
        return success_response(data=data, status_code=status.HTTP_201_CREATED)

    logger.info(
        "MUJEB_CREATE duplicate client_id=%s company_id=%s phone=%s external_id=%s",
        data.get("client_id"),
        company.id,
        _phone_log(phone),
        external_id or "-",
    )
    logger.info("MUJEB_CREATE completed status=200 company_id=%s", company.id)
    return success_response(data=data, status_code=status.HTTP_200_OK)


@extend_schema(
    tags=["Mujeb"],
    request=MujebCheckLeadSerializer,
    responses={
        200: inline_serializer(
            name="MujebLeadCheck",
            fields={
                "success": drf_serializers.BooleanField(),
                "data": drf_serializers.DictField(),
            },
        ),
    },
    auth=[],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@rate_limit_webhook(max_requests=120, window=60)
def mujeb_check_lead_view(request):
    """
    POST /api/v1/integrations/leads/mujeb/check/
    Auth: Authorization: Bearer <company_lead_api_key> or X-Lead-Api-Key header.
    Read-only: does a lead already exist for phone and/or external_id?
    """
    ip = _client_ip(request)
    logger.info("MUJEB_CHECK_CALLED method=%s ip=%s", request.method, ip)

    company, auth_err = _resolve_lead_api_company(request, action="CHECK")
    if auth_err:
        return auth_err

    body, parse_err = _parse_json_body(request, action="CHECK")
    if parse_err:
        return parse_err

    serializer = MujebCheckLeadSerializer(data=body)
    if not serializer.is_valid():
        logger.warning(
            "MUJEB_CHECK validation failed company_id=%s errors=%s",
            company.id,
            serializer.errors,
        )
        return validation_error_response(serializer.errors)

    phone = serializer.validated_data.get("phone") or None
    external_id = serializer.validated_data.get("external_id") or None
    logger.info(
        "MUJEB_CHECK processing company_id=%s phone=%s external_id=%s",
        company.id,
        _phone_log(phone),
        external_id or "-",
    )

    data = check_inbound_lead_exists(
        company,
        phone=phone,
        external_id=external_id,
    )
    if data.get("exists"):
        logger.info(
            "MUJEB_CHECK exists=true matched_by=%s client_id=%s company_id=%s",
            data.get("matched_by"),
            data.get("client_id"),
            company.id,
        )
    else:
        logger.info("MUJEB_CHECK exists=false company_id=%s", company.id)
    logger.info("MUJEB_CHECK completed status=200 company_id=%s", company.id)
    return success_response(data=data, status_code=status.HTTP_200_OK)


def _mujeb_config_data(company) -> dict:
    account = IntegrationAccount.objects.filter(
        company=company,
        platform=IntegrationPlatform.MUJEB,
        external_account_id=f"mujeb_{company.id}",
    ).first()
    metadata = account.metadata if account and isinstance(account.metadata, dict) else {}
    keys = CompanyLeadApiKey.objects.filter(company=company, is_active=True).order_by("-created_at")
    return {
        "endpoint_url": _mujeb_endpoint_url(),
        "check_endpoint_url": _mujeb_check_endpoint_url(),
        "documentation_path": "/docs/MUJEB_INTEGRATION.md",
        "keys": [_serialize_key_row(k) for k in keys],
        "integration_status": account.status if account else "disconnected",
        "last_received_at": metadata.get("last_received_at"),
        "last_sync_at": account.last_sync_at.isoformat() if account and account.last_sync_at else None,
    }


class MujebConfigView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def get(self, request):
        company = request.user.company
        if not company:
            return error_response("Company is required.", code="company_required", status_code=400)
        return success_response(data=_mujeb_config_data(company))
