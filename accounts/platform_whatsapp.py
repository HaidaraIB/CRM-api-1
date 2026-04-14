"""
Send WhatsApp via the platform Cloud API number (signup OTP + admin → tenant owner).
"""
import logging
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def normalize_phone_digits(phone: str) -> str:
    """Digits only, no leading + (matches inbound WhatsApp 'from' format)."""
    if not phone:
        return ""
    return "".join(c for c in str(phone).replace(" ", "") if c.isdigit())


def platform_whatsapp_configured() -> bool:
    return bool(
        getattr(settings, "PLATFORM_WHATSAPP_PHONE_NUMBER_ID", "")
        and getattr(settings, "PLATFORM_WHATSAPP_ACCESS_TOKEN", "")
    )


def _graph_base() -> str:
    ver = getattr(settings, "PLATFORM_WHATSAPP_GRAPH_API_VERSION", "v21.0") or "v21.0"
    return f"https://graph.facebook.com/{ver}"


def _post_messages(payload: dict) -> tuple[bool, Any]:
    phone_number_id = getattr(settings, "PLATFORM_WHATSAPP_PHONE_NUMBER_ID", "")
    token = getattr(settings, "PLATFORM_WHATSAPP_ACCESS_TOKEN", "")
    if not phone_number_id or not token:
        return False, {"error": "platform_whatsapp_not_configured"}
    url = f"{_graph_base()}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
    except requests.RequestException as e:
        logger.warning("Platform WhatsApp request error: %s", e)
        return False, {"error": str(e)}
    try:
        data = resp.json()
    except ValueError:
        data = {"error": resp.text or str(resp.status_code)}
    if resp.status_code >= 400:
        logger.warning("Platform WhatsApp Graph error: status=%s body=%s", resp.status_code, data)
        return False, data
    return True, data


def send_otp_template(to_digits: str, code: str) -> tuple[bool, Any]:
    """
    Send authentication template with one body placeholder = OTP code.
    Template name/lang from settings must match an approved Meta template.
    """
    name = getattr(settings, "PLATFORM_WHATSAPP_OTP_TEMPLATE_NAME", "") or ""
    lang = getattr(settings, "PLATFORM_WHATSAPP_OTP_TEMPLATE_LANG", "en") or "en"
    if not name:
        return False, {"error": "otp_template_not_configured"}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_digits,
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(code)[:32]}],
                }
            ],
        },
    }
    return _post_messages(payload)


def send_text_message(to_digits: str, body: str) -> tuple[bool, Any]:
    """Session message (24h window after user messaged this business number)."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_digits,
        "type": "text",
        "text": {"body": (body or "")[:4096]},
    }
    return _post_messages(payload)


def send_template_with_body_text(to_digits: str, template_name: str, lang: str, body_text: str) -> tuple[bool, Any]:
    """Single body-parameter utility / marketing template."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_digits,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": (body_text or "")[:1024]}],
                }
            ],
        },
    }
    return _post_messages(payload)


def send_admin_message(to_digits: str, body: str) -> tuple[bool, Any]:
    """
    If PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME is set, use that template (works outside 24h session).
    Otherwise send a plain text session message (requires an open customer-care window).
    """
    admin_tpl = (getattr(settings, "PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME", "") or "").strip()
    admin_lang = (getattr(settings, "PLATFORM_WHATSAPP_ADMIN_TEMPLATE_LANG", "en") or "en").strip()
    if admin_tpl:
        return send_template_with_body_text(to_digits, admin_tpl, admin_lang, body)
    return send_text_message(to_digits, body)
