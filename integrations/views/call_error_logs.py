"""Owner-only WhatsApp call error logs + authenticated client error reporting."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription, IsAdmin
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.services.whatsapp_call_error_logs import (
    fetch_call_error_logs,
    log_whatsapp_call_error,
)
from integrations.whatsapp_access import user_can_access_whatsapp_calls
from integrations.models import WhatsAppCallErrorSource


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription, IsAdmin])
def whatsapp_call_error_logs_list(request):
    """
    Company-wide WhatsApp call failure feed for Messaging Center (owner-only).
    GET /api/integrations/whatsapp/call-error-logs/
    """
    company = request.user.company
    if not company:
        return success_response(
            data={
                "count": 0,
                "page": 1,
                "page_size": 30,
                "summary": {},
                "results": [],
            }
        )
    data = fetch_call_error_logs(company, request.query_params)
    return success_response(data=data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def whatsapp_call_client_errors(request):
    """
    Agents report browser-side call failures (mic / WebRTC) for the owner log.
    POST /api/integrations/whatsapp/calls/client-errors/
    """
    if not user_can_access_whatsapp_calls(request.user):
        return error_response(
            "WhatsApp call access is disabled for your account",
            code="whatsapp_access_disabled",
            status_code=403,
        )
    company = request.user.company
    if not company:
        return error_response("No company", status_code=400)

    error_code = (request.data.get("error_code") or "").strip()[:128]
    error_message = (request.data.get("error_message") or "").strip()[:4000]
    peer_phone = (request.data.get("to") or request.data.get("peer_phone") or "").strip()
    client_id = request.data.get("client") or request.data.get("client_id")
    source_raw = (request.data.get("source") or WhatsAppCallErrorSource.MIC.value).strip().lower()
    if source_raw not in (
        WhatsAppCallErrorSource.MIC.value,
        WhatsAppCallErrorSource.WEBRTC.value,
    ):
        source_raw = WhatsAppCallErrorSource.MIC.value

    if not error_code and not error_message:
        return validation_error_response(
            {"error_code": ["Required"], "error_message": ["Required"]}
        )

    client = None
    if client_id:
        from crm.models import Client

        client = Client.objects.filter(company=company, pk=client_id).first()

    row = log_whatsapp_call_error(
        company=company,
        source=source_raw,
        error_code=error_code or "whatsapp_mic_permission_denied",
        error_message=error_message or error_code,
        agent=request.user,
        client=client,
        peer_phone=peer_phone,
        meta_details={
            "client_reported": True,
            "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:500],
        },
    )
    return success_response(
        data={"id": row.id if row else None},
        status_code=201,
    )
