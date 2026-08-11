"""
Send an approved WhatsApp MessageTemplate via Meta Cloud API (no HTTP request context).
Shared by Messaging Center send-template and automated new-lead welcome.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from integrations.models import LeadWhatsAppMessage, MessageSendSource, MessageTemplate
from integrations.oauth_utils import META_GRAPH_API_BASE_URL
from integrations.views.templates_whatsapp import (
    build_whatsapp_template_components_for_client,
    count_template_body_placeholders,
    meta_slug_template_name,
    template_body_parameter_values,
)
from integrations.whatsapp_account_sync import resolve_whatsapp_account_for_api

logger = logging.getLogger(__name__)


def normalize_whatsapp_to_digits(phone: str) -> str:
    """Normalize recipient for Cloud API: Iraq 07… → 964…, strip +, spaces."""
    from integrations.services.phone_match import digits_only
    from integrations.services.twilio_phone import normalize_phone_to_e164

    raw = (phone or "").strip()
    if not raw:
        return ""
    return digits_only(normalize_phone_to_e164(raw))


def template_outbound_log_body(template: MessageTemplate, param_values: Optional[list] = None) -> str:
    """Human-readable body for chat history after sending a Meta template."""
    import re

    from integrations.views.templates_whatsapp import _find_placeholders_in_order

    meta_name = meta_slug_template_name(template.name, template.id)
    content = (template.content or "").strip()
    if content.lower().startswith("(imported from meta:"):
        content = ""
    if param_values and content:
        out = content
        matches = _find_placeholders_in_order(out)
        if matches:
            parts = []
            last = 0
            for i, (start, end, _canonical, _sample, _getter) in enumerate(matches):
                parts.append(out[last:start])
                parts.append(str(param_values[i]) if i < len(param_values) else "-")
                last = end
            parts.append(out[last:])
            out = "".join(parts)
        for i, val in enumerate(param_values, start=1):
            out = re.sub(rf"\{{\{{\s*{i}\s*\}}\}}", str(val), out)
        return out[:65535]
    if content:
        return content[:65535]
    return f"[Template: {meta_name}]"


def send_approved_whatsapp_template(
    *,
    company,
    template: MessageTemplate,
    to_phone: str,
    client=None,
    phone_number_id: Optional[str] = None,
    send_source: str = MessageSendSource.MANUAL,
    created_by=None,
    campaign_batch=None,
    persist_message: bool = True,
) -> tuple[bool, Optional[str], Optional[str], Optional[dict]]:
    """
    Send an APPROVED WhatsApp template via Graph API.

    Returns (ok, wam_id, error_key, graph_data_or_error).
    Does not enforce plan quotas — callers must check usage before calling.
    """
    to = normalize_whatsapp_to_digits(to_phone)
    if not to or not to.isdigit():
        return False, None, "invalid_phone", None

    ch = (template.channel_type or "").lower()
    if ch not in ("whatsapp", "whatsapp_api"):
        return False, None, "not_whatsapp_template", None

    meta_st = (template.meta_status or "").upper()
    if meta_st and meta_st != "APPROVED":
        return False, None, "whatsapp_template_not_approved", None

    n_placeholders = count_template_body_placeholders(template.content or "")
    header_needs = count_template_body_placeholders(getattr(template, "header_text", None) or "")
    param_values: list[str] = []
    if n_placeholders > 0 or header_needs > 0:
        if client is None:
            return False, None, "client_required_for_placeholders", None
        param_values = template_body_parameter_values(template, client)
        if n_placeholders > 0 and len(param_values) != n_placeholders:
            return False, None, "whatsapp_template_parameter_count", None

    wa_account, wa_err = resolve_whatsapp_account_for_api(company, phone_number_id)
    if not wa_account:
        return False, None, wa_err or "no_connected_whatsapp_number", None

    access_token = wa_account.get_access_token()
    if not access_token:
        return False, None, "whatsapp_no_access_token", None

    language = (getattr(template, "language", None) or "en_US").strip() or "en_US"
    meta_name = meta_slug_template_name(template.name, template.id)
    template_block: dict[str, Any] = {
        "name": meta_name,
        "language": {"code": language},
    }
    if client is not None:
        components = build_whatsapp_template_components_for_client(
            template, client, body_param_values=param_values if param_values else None
        )
        if components:
            template_block["components"] = components
    elif param_values:
        template_block["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p[:1024]} for p in param_values],
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

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.warning(
            "WhatsApp template send request error: phone_number_id=%s to=%s error=%s",
            wa_account.phone_number_id,
            to[-4:] if len(to) > 4 else to,
            e,
        )
        return False, None, "whatsapp_api_request_failed", {"error": str(e)}

    if resp.status_code >= 400:
        try:
            err_body = resp.json()
        except Exception:
            err_body = {"error": getattr(resp, "text", "") or str(resp)}
        logger.warning(
            "WhatsApp template send failed: graph_status=%s template=%s language=%s body=%s",
            resp.status_code,
            meta_name,
            language,
            err_body,
        )
        return False, None, "whatsapp_api_request_failed", err_body if isinstance(err_body, dict) else None

    try:
        data = resp.json()
    except ValueError:
        return False, None, "whatsapp_api_invalid_json", None

    wam_id = None
    if isinstance(data.get("messages"), list) and data["messages"]:
        wam_id = data["messages"][0].get("id")

    if persist_message and client is not None:
        preview = template_outbound_log_body(template, param_values if param_values else None)
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
                send_source=send_source
                if send_source in MessageSendSource.values
                else MessageSendSource.MANUAL,
                campaign_batch=campaign_batch,
            )
        except Exception:
            logger.exception(
                "Failed to persist outbound WhatsApp template client_id=%s",
                getattr(client, "id", None),
            )

    return True, wam_id, None, data
