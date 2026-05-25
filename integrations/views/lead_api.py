"""
Custom Lead API: inbound lead creation and authenticated key management.
"""
import json
import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.decorators import rate_limit_webhook
from integrations.lead_api_keys import extract_lead_api_key_from_request, resolve_active_api_key
from integrations.models import CompanyLeadApiKey, IntegrationAccount
from integrations.serializers_lead_api import InboundLeadSerializer
from integrations.services.inbound_lead import create_inbound_lead, get_or_create_lead_api_account

logger = logging.getLogger(__name__)


def _inbound_endpoint_url() -> str:
    base = getattr(settings, "API_BASE_URL", "").rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/integrations/leads/inbound/"
    if base.endswith("/api"):
        return f"{base}/v1/integrations/leads/inbound/"
    if base:
        return f"{base}/api/v1/integrations/leads/inbound/"
    return "/api/v1/integrations/leads/inbound/"


@extend_schema(
    tags=["Lead API"],
    request=InboundLeadSerializer,
    responses={
        201: inline_serializer(
            name="InboundLeadCreated",
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
def inbound_lead_view(request):
    """
    POST /api/v1/integrations/leads/inbound/
    Auth: Authorization: Bearer <company_lead_api_key> or X-Lead-Api-Key header.
    """
    raw_key = extract_lead_api_key_from_request(request)
    if not raw_key:
        return error_response(
            "API key is required. Use Authorization: Bearer <key> or X-Lead-Api-Key.",
            code="missing_api_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    key_row = resolve_active_api_key(raw_key)
    if not key_row:
        return error_response(
            "Invalid or inactive API key.",
            code="invalid_api_key",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    company = key_row.company
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return error_response(
            "Invalid JSON body.",
            code="invalid_json",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    serializer = InboundLeadSerializer(data=body, company=company)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    account = get_or_create_lead_api_account(company)
    try:
        data, created = create_inbound_lead(
            company=company,
            account=account,
            payload=serializer.validated_data,
        )
    except Exception as exc:
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
        logger.exception("Lead API inbound error company_id=%s", company.id)
        return error_response(
            "Failed to create lead.",
            code="lead_create_failed",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if created:
        return success_response(data=data, status_code=status.HTTP_201_CREATED)
    return success_response(data=data, status_code=status.HTTP_200_OK)


def _require_company_admin(user):
    if not user or not getattr(user, "is_admin", lambda: False)():
        return error_response(
            "Only company administrators can manage Lead API keys.",
            code="admin_required",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return None


def _serialize_key_row(row: CompanyLeadApiKey) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "key_suffix": row.key_suffix or "",
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


def _lead_api_config_data(company) -> dict:
    account = IntegrationAccount.objects.filter(
        company=company,
        platform="api",
        external_account_id=f"lead_api_{company.id}",
    ).first()
    metadata = account.metadata if account and isinstance(account.metadata, dict) else {}
    keys = CompanyLeadApiKey.objects.filter(company=company, is_active=True).order_by("-created_at")
    return {
        "endpoint_url": _inbound_endpoint_url(),
        "documentation_path": "/docs/CUSTOM_LEAD_API.md",
        "keys": [_serialize_key_row(k) for k in keys],
        "integration_status": account.status if account else "disconnected",
        "last_received_at": metadata.get("last_received_at"),
        "last_sync_at": account.last_sync_at.isoformat() if account and account.last_sync_at else None,
    }


class LeadApiConfigView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def get(self, request):
        company = request.user.company
        if not company:
            return error_response("Company is required.", code="company_required", status_code=400)
        return success_response(data=_lead_api_config_data(company))


class LeadApiKeyCreateView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def post(self, request):
        denied = _require_company_admin(request.user)
        if denied:
            return denied
        company = request.user.company
        name = (request.data.get("name") or "").strip()
        if not name:
            return error_response("name is required.", code="name_required", status_code=400)

        from integrations.lead_api_keys import generate_lead_api_key

        full_key, prefix, suffix, key_hash = generate_lead_api_key()
        row = CompanyLeadApiKey.objects.create(
            company=company,
            name=name,
            key_prefix=prefix,
            key_suffix=suffix,
            key_hash=key_hash,
            created_by=request.user,
        )
        get_or_create_lead_api_account(company)
        data = _serialize_key_row(row)
        data["api_key"] = full_key
        return success_response(
            data=data,
            message="Store this API key securely; it will not be shown again.",
            status_code=status.HTTP_201_CREATED,
        )


class LeadApiKeyRotateView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def post(self, request, key_id: int):
        denied = _require_company_admin(request.user)
        if denied:
            return denied
        company = request.user.company
        try:
            row = CompanyLeadApiKey.objects.get(id=key_id, company=company)
        except CompanyLeadApiKey.DoesNotExist:
            return error_response("API key not found.", code="not_found", status_code=404)

        from integrations.lead_api_keys import generate_lead_api_key

        full_key, prefix, suffix, key_hash = generate_lead_api_key()
        row.key_prefix = prefix
        row.key_suffix = suffix
        row.key_hash = key_hash
        row.is_active = True
        row.save(update_fields=["key_prefix", "key_suffix", "key_hash", "is_active"])
        data = _serialize_key_row(row)
        data["api_key"] = full_key
        return success_response(
            data=data,
            message="New API key generated. Update your integrations; the old key no longer works.",
        )


class LeadApiKeyRevokeView(APIView):
    permission_classes = [IsAuthenticated, HasActiveSubscription]

    def delete(self, request, key_id: int):
        denied = _require_company_admin(request.user)
        if denied:
            return denied
        company = request.user.company
        updated = CompanyLeadApiKey.objects.filter(id=key_id, company=company).update(is_active=False)
        if not updated:
            return error_response("API key not found.", code="not_found", status_code=404)
        return success_response(message="API key revoked.")
