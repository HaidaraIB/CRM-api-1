"""Send an approved WhatsApp template message via the Meta Graph API.

Extracted from integrations/views/webhooks_messaging.py::whatsapp_send_template
so it can be called from a worker process (Messaging Center campaign task,
integrations/tasks.py) with no request/response objects and no dependency on
per-request access-control checks - those stay in the HTTP view.
"""
from __future__ import annotations

import logging

import requests

from ..models import IntegrationLog, LeadWhatsAppMessage
from ..oauth_utils import META_GRAPH_API_BASE_URL
from ..views.templates_whatsapp import (
    build_whatsapp_template_components_for_client,
    meta_slug_template_name,
)
from ..views.webhooks_messaging import (
    _api_code_from_graph_error,
    _redact_phone_e164,
    _resolve_whatsapp_client,
    _template_outbound_log_body,
)

logger = logging.getLogger(__name__)


def send_whatsapp_template_message(
    company,
    wa_account,
    *,
    to: str,
    template,
    param_values=None,
    fill_client=None,
    sender_name: str = "",
    created_by=None,
    send_source: str = "manual",
    campaign_batch=None,
):
    """Pure send primitive: calls the Graph API with an already-resolved
    WhatsApp account, logs the outbound message, and increments usage. No
    quota/entitlement checks, no access-token resolution, and no
    request.user coupling beyond the display name string passed in - safe
    to call from a worker process with no request context. Callers resolve
    `wa_account` themselves (via resolve_whatsapp_account_for_api) so
    account-not-found/no-access-token errors keep their existing
    status-code handling at the call site.

    Returns (ok, external_message_id, error_key, error_message, details).
    """
    access_token = wa_account.get_access_token()
    if not access_token:
        return False, None, "whatsapp_no_access_token", "WhatsApp account has no access token", None

    language = (getattr(template, "language", None) or "en_US").strip() or "en_US"
    meta_name = meta_slug_template_name(template.name, template.id)
    template_block = {"name": meta_name, "language": {"code": language}}
    if fill_client is not None:
        components = build_whatsapp_template_components_for_client(
            template,
            fill_client,
            body_param_values=param_values if param_values else None,
            sender_name=sender_name,
        )
        if components:
            template_block["components"] = components
    elif param_values:
        template_block["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)[:1024]} for p in param_values],
            }
        ]

    url = f"{META_GRAPH_API_BASE_URL}/{wa_account.phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": template_block,
    }
    redacted_to = _redact_phone_e164(to)
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.warning(
            "WhatsApp template send request error: phone_number_id=%s waba_id=%s to=%s error=%s",
            wa_account.phone_number_id, wa_account.waba_id, redacted_to, e,
        )
        return False, None, "bad_request", str(e), {"graph_http_status": None}

    graph_status = resp.status_code
    if graph_status >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"error": getattr(resp, "text", "") or str(resp)}
        if isinstance(err_body, dict):
            err_body["graph_http_status"] = graph_status
            err_body["crm_template_name"] = meta_name
            err_body["crm_template_language"] = language
            err_body["crm_waba_id"] = wa_account.waba_id
            err_body["crm_phone_number_id"] = wa_account.phone_number_id
        else:
            err_body = {"error": str(err_body), "graph_http_status": graph_status}
        logger.warning(
            "WhatsApp template send failed: graph_status=%s phone_number_id=%s waba_id=%s "
            "template=%s language=%s to=%s body=%s",
            graph_status, wa_account.phone_number_id, wa_account.waba_id, meta_name, language, redacted_to, err_body,
        )
        error_key = _api_code_from_graph_error(err_body)
        error_message = err_body.get("error") if isinstance(err_body.get("error"), str) else "WhatsApp API request failed."
        return False, None, error_key, error_message, err_body

    try:
        data = resp.json()
    except ValueError:
        return False, None, "bad_request", "WhatsApp API returned invalid JSON.", {"graph_http_status": graph_status}

    wam_id = (data.get("messages") or [{}])[0].get("id") if isinstance(data.get("messages"), list) else None
    logger.info(
        "WhatsApp template send ok: graph_status=%s phone_number_id=%s to=%s wam_id=%s template=%s",
        graph_status, wa_account.phone_number_id, redacted_to, wam_id, meta_name,
    )

    from subscriptions.entitlements import increment_monthly_usage

    increment_monthly_usage(company, "monthly_whatsapp_messages", requested_delta=1)
    if wa_account.integration_account_id:
        IntegrationLog.objects.create(
            account_id=wa_account.integration_account_id,
            action="whatsapp_template_sent",
            status="success",
            message=f"Template {meta_name} sent to {to}",
            response_data=data,
        )

    preview = _template_outbound_log_body(template, param_values if param_values else None)
    client = fill_client or _resolve_whatsapp_client(
        company, None, to, integration_account=wa_account.integration_account, create_if_missing=True,
    )
    if client:
        try:
            LeadWhatsAppMessage.objects.create(
                client=client,
                phone_number=to,
                body=preview[:65535],
                direction=LeadWhatsAppMessage.DIRECTION_OUTBOUND,
                whatsapp_message_id=wam_id,
                phone_number_id=wa_account.phone_number_id,
                delivery_status="sent",
                created_by=created_by,
                send_source=send_source,
                campaign_batch=campaign_batch,
            )
        except Exception:
            logger.exception("Failed to persist outbound WhatsApp template client_id=%s", getattr(client, "id", None))

    return True, wam_id, None, None, data
