"""
Send WhatsApp via the platform Cloud API number (signup OTP + admin → tenant owner).
DB singleton PlatformWhatsAppSettings overrides django.conf env when fields are set.
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


def _db_whatsapp_row():
    try:
        from settings.models import PlatformWhatsAppSettings

        return PlatformWhatsAppSettings.get_settings()
    except Exception:
        return None


def effective_platform_phone_number_id() -> str:
    row = _db_whatsapp_row()
    if row and (row.phone_number_id or "").strip():
        return row.phone_number_id.strip()
    return (getattr(settings, "PLATFORM_WHATSAPP_PHONE_NUMBER_ID", "") or "").strip()


def effective_platform_access_token() -> str:
    row = _db_whatsapp_row()
    if row:
        tok = row.get_access_token()
        if tok:
            return tok
    return (getattr(settings, "PLATFORM_WHATSAPP_ACCESS_TOKEN", "") or "").strip()


def effective_graph_api_version() -> str:
    row = _db_whatsapp_row()
    if row and (row.graph_api_version or "").strip():
        return row.graph_api_version.strip()
    return (getattr(settings, "PLATFORM_WHATSAPP_GRAPH_API_VERSION", "v21.0") or "v21.0").strip()


def effective_otp_template_name() -> str:
    row = _db_whatsapp_row()
    if row and (row.otp_template_name or "").strip():
        return row.otp_template_name.strip()
    return (getattr(settings, "PLATFORM_WHATSAPP_OTP_TEMPLATE_NAME", "") or "").strip()


def effective_otp_template_lang() -> str:
    row = _db_whatsapp_row()
    if row and (row.otp_template_lang or "").strip():
        return row.otp_template_lang.strip()
    return (getattr(settings, "PLATFORM_WHATSAPP_OTP_TEMPLATE_LANG", "en") or "en").strip()


def effective_admin_template_name() -> str:
    row = _db_whatsapp_row()
    if row and (row.admin_template_name or "").strip():
        return row.admin_template_name.strip()
    return (getattr(settings, "PLATFORM_WHATSAPP_ADMIN_TEMPLATE_NAME", "") or "").strip()


def effective_admin_template_lang() -> str:
    row = _db_whatsapp_row()
    if row and (row.admin_template_lang or "").strip():
        return row.admin_template_lang.strip()
    return (getattr(settings, "PLATFORM_WHATSAPP_ADMIN_TEMPLATE_LANG", "en") or "en").strip()


def platform_whatsapp_configured() -> bool:
    return bool(effective_platform_phone_number_id() and effective_platform_access_token())


def _graph_base() -> str:
    ver = effective_graph_api_version() or "v21.0"
    return f"https://graph.facebook.com/{ver}"


def _post_messages(payload: dict) -> tuple[bool, Any]:
    phone_number_id = effective_platform_phone_number_id()
    token = effective_platform_access_token()
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
    Template name/lang from DB or env must match an approved Meta template.
    """
    name = effective_otp_template_name()
    lang = effective_otp_template_lang()
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
    If admin template name is set (DB or env), use that template (works outside 24h session).
    Otherwise send a plain text session message (requires an open customer-care window).
    """
    admin_tpl = effective_admin_template_name()
    admin_lang = effective_admin_template_lang()
    if admin_tpl:
        return send_template_with_body_text(to_digits, admin_tpl, admin_lang, body)
    return send_text_message(to_digits, body)
