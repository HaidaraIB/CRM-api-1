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


def _resolve_lead_api_company(request):
    """Return (company, error_response). error_response is set on auth failure."""
    raw_key = extract_lead_api_key_from_request(request)
    if not raw_key:
        return None, error_response(
            "API key is required. Use Authorization: Bearer <key> or X-Lead-Api-Key.",
            code="missing_api_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    key_row = resolve_active_api_key(raw_key)
    if not key_row:
        return None, error_response(
            "Invalid or inactive API key.",
            code="invalid_api_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return key_row.company, None


def _parse_json_body(request):
    """Return (body_dict, error_response)."""
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
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
            return error_response(str(message), code=str(code), status_code=status_code, details=detail)
        return validation_error_response(detail)
    logger.exception("Mujeb inbound error company_id=%s", company_id)
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
    company, auth_err = _resolve_lead_api_company(request)
    if auth_err:
        return auth_err

    body, parse_err = _parse_json_body(request)
    if parse_err:
        return parse_err

    serializer = InboundLeadSerializer(data=body, company=company)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    account = get_or_create_mujeb_account(company)
    try:
        data, created = create_inbound_lead(
            company=company,
            account=account,
            payload=serializer.validated_data,
            source="mujeb",
            platform_gate=MUJEB_PLATFORM,
        )
    except Exception as exc:
        return _handle_inbound_create_errors(exc, company.id)

    if created:
        return success_response(data=data, status_code=status.HTTP_201_CREATED)
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
    company, auth_err = _resolve_lead_api_company(request)
    if auth_err:
        return auth_err

    body, parse_err = _parse_json_body(request)
    if parse_err:
        return parse_err

    serializer = MujebCheckLeadSerializer(data=body)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    data = check_inbound_lead_exists(
        company,
        phone=serializer.validated_data.get("phone") or None,
        external_id=serializer.validated_data.get("external_id") or None,
    )
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
