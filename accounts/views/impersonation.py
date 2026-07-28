from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from crm_saas_api.responses import error_response, success_response, validation_error_response
from ..models import ImpersonationSession
from ..serializers import ImpersonateSerializer, build_user_auth_payload
from ..permissions import IsSuperAdmin, is_impersonating
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
import secrets
import logging

from ..services import get_client_ip

logger = logging.getLogger(__name__)

# Code must be exchanged within this window after create.
IMPERSONATION_CODE_TTL_SECONDS = 120
# After first successful exchange, allow the same code to be re-fetched for this long
# (React Strict Mode / double-mount / flaky networks).
IMPERSONATION_EXCHANGE_GRACE_SECONDS = 60


def _build_impersonation_tokens(target_user, impersonator, session_id):
    """Issue owner JWTs marked as an impersonation session."""
    refresh = RefreshToken.for_user(target_user)
    refresh["impersonation"] = True
    refresh["impersonator_id"] = impersonator.id
    refresh["impersonation_sid"] = session_id
    access = refresh.access_token
    return str(access), str(refresh)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsSuperAdmin])
def impersonate(request):
    """
    Super admin only: obtain JWT tokens as a company owner (impersonation).
    POST /api/auth/impersonate/
    Body: { "user_id": <id> } or { "company_id": <id> }
    Response: { "access", "refresh", "user", "impersonated_by", "impersonation", "impersonation_code" }
    """
    serializer = ImpersonateSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    target_user = serializer.validated_data["target_user"]
    company = serializer.validated_data.get("company")
    user_payload = build_user_auth_payload(target_user, request)
    impersonated_by = {
        "id": request.user.id,
        "username": request.user.username,
        "email": request.user.email,
    }

    impersonation_code = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(seconds=IMPERSONATION_CODE_TTL_SECONDS)

    # Create row first so JWT can carry impersonation_sid (= pk).
    session = ImpersonationSession.objects.create(
        code=impersonation_code,
        payload={},
        expires_at=expires_at,
        impersonator=request.user,
        target_user=target_user,
        company=company,
    )

    access, refresh = _build_impersonation_tokens(target_user, request.user, session.id)
    impersonation_meta = {
        "active": True,
        "sid": session.id,
        "company_id": company.id if company else None,
        "company_name": company.name if company else (user_payload.get("company_name") or ""),
        "target_user": {
            "id": target_user.id,
            "username": target_user.username,
            "email": target_user.email,
            "first_name": target_user.first_name or "",
            "last_name": target_user.last_name or "",
        },
        "impersonated_by": impersonated_by,
    }
    payload = {
        "access": access,
        "refresh": refresh,
        "user": user_payload,
        "impersonated_by": impersonated_by,
        "impersonation": impersonation_meta,
    }
    session.payload = payload
    session.save(update_fields=["payload"])

    cache.set(f"impersonate:{impersonation_code}", payload, timeout=IMPERSONATION_CODE_TTL_SECONDS)

    try:
        from settings.services import log_system_action

        log_system_action(
            action="impersonation_start",
            user=request.user,
            message=(
                f"Super admin {request.user.email} impersonated "
                f"{target_user.email} ({target_user.username})"
            ),
            metadata={
                "target_user_id": target_user.id,
                "target_username": target_user.username,
                "target_email": target_user.email,
                "company_id": company.id if company else None,
                "company_name": company.name if company else None,
                "impersonation_sid": session.id,
            },
            ip_address=get_client_ip(request),
        )
    except Exception as e:
        logger.warning("Failed to write impersonation audit log: %s", e)

    return success_response(
        data={
            **payload,
            "impersonation_code": impersonation_code,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def impersonate_exchange_status(request):
    """Diagnostic: GET /api/auth/impersonate-exchange/status/ returns 200 if this app revision is deployed."""
    return success_response(
        data={"status": "ok", "endpoint": "impersonate-exchange"},
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def impersonate_exchange(request):
    """
    Exchange a one-time impersonation code for tokens (used by CRM app after redirect).
    GET /api/auth/impersonate-exchange/?code=<impersonation_code>
    Returns: { "access", "refresh", "user", "impersonated_by", "impersonation" }.
    First use marks used_at; re-fetch within grace window returns the same payload.
    """
    logger.info("impersonate_exchange view called for path=%s", request.path)
    code = request.query_params.get("code", "").strip()
    if not code:
        return error_response("Missing code parameter.", code="missing_parameter")

    now = timezone.now()
    grace = timedelta(seconds=IMPERSONATION_EXCHANGE_GRACE_SECONDS)

    with transaction.atomic():
        session = (
            ImpersonationSession.objects.select_for_update()
            .filter(code=code)
            .first()
        )
        if session:
            if session.used_at:
                if session.used_at + grace >= now:
                    return success_response(data=session.payload)
                return error_response(
                    "Invalid or expired code.",
                    code="invalid_or_expired_code",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            if session.expires_at <= now:
                ImpersonationSession.objects.filter(expires_at__lte=now, used_at__isnull=True).delete()
                return error_response(
                    "Invalid or expired code.",
                    code="invalid_or_expired_code",
                    details=(
                        {"hint": "Code was found but expired (expires_at <= now)."}
                        if settings.DEBUG
                        else None
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            session.used_at = now
            session.save(update_fields=["used_at"])
            cache.delete(f"impersonate:{code}")
            return success_response(data=session.payload)

    # Legacy cache-only fallback (same process / older rows without new fields path)
    data = cache.get(f"impersonate:{code}")
    if data:
        cache.delete(f"impersonate:{code}")
        return success_response(data=data)

    logger.warning(
        "impersonate_exchange: code not found or expired (code=%s..., session_found=%s)",
        code[:12] if len(code) > 12 else code,
        False,
    )
    ImpersonationSession.objects.filter(expires_at__lte=now, used_at__isnull=True).delete()
    hint = None
    if settings.DEBUG:
        hint = "Code not in DB. Ensure dashboard and admin panel use the same API URL."
    return error_response(
        "Invalid or expired code.",
        code="invalid_or_expired_code",
        details={"hint": hint} if hint else None,
        status_code=status.HTTP_404_NOT_FOUND,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def impersonate_end(request):
    """
    End an active impersonation session (CRM Exit).
    POST /api/auth/impersonate-end/
    Optional body: { "refresh": "<refresh token>" } to blacklist it.
    """
    if not is_impersonating(request):
        return error_response(
            "Not an impersonation session.",
            code="not_impersonating",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    token = getattr(request, "auth", None)
    sid = None
    impersonator_id = None
    try:
        if token is not None:
            sid = token.get("impersonation_sid")
            impersonator_id = token.get("impersonator_id")
    except Exception:
        pass

    refresh_raw = (request.data or {}).get("refresh")
    if refresh_raw:
        try:
            RefreshToken(refresh_raw).blacklist()
        except Exception as e:
            logger.info("impersonate_end: could not blacklist refresh: %s", e)

    try:
        from settings.services import log_system_action

        log_system_action(
            action="impersonation_end",
            user=request.user,
            message=(
                f"Impersonation ended for {request.user.email} "
                f"(impersonator_id={impersonator_id}, sid={sid})"
            ),
            metadata={
                "target_user_id": request.user.id,
                "impersonator_id": impersonator_id,
                "impersonation_sid": sid,
            },
            ip_address=get_client_ip(request),
        )
    except Exception as e:
        logger.warning("Failed to write impersonation_end audit log: %s", e)

    return success_response(
        data={"ended": True, "impersonation_sid": sid},
        message="Impersonation session ended.",
    )
