"""
WhatsApp coexistence (WhatsApp Business app onboarding) helpers.

Meta docs:
https://developers.facebook.com/docs/whatsapp/embedded-signup/custom-flows/onboarding-business-app-users/
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from integrations.oauth_utils import META_GRAPH_API_BASE_URL

logger = logging.getLogger(__name__)

# Must be subscribed in Meta App Dashboard → WhatsApp → Configuration (in addition to messages).
COEXISTENCE_WEBHOOK_FIELDS = (
    "history",
    "smb_app_state_sync",
    "smb_message_echoes",
    "account_update",
)

FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING = "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING"
HISTORY_NOT_SHARED_ERROR_CODE = 2593109


def is_coexistence_signup_event(signup_event: Optional[str]) -> bool:
    return (signup_event or "").strip() == FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING


def subscribe_waba_webhooks(access_token: str, waba_id: str) -> bool:
    """
    POST /{waba-id}/subscribed_apps — required for Tech Provider Embedded Signup webhooks.
    """
    waba_id = (waba_id or "").strip()
    token = (access_token or "").strip()
    if not waba_id or not token:
        return False
    url = f"{META_GRAPH_API_BASE_URL}/{waba_id}/subscribed_apps"
    try:
        resp = requests.post(url, params={"access_token": token}, timeout=20)
        if resp.status_code >= 400:
            logger.warning(
                "WhatsApp WABA subscribe failed: waba_id=%s status=%s body=%s",
                waba_id,
                resp.status_code,
                (resp.text or "")[:500],
            )
            return False
        logger.info("WhatsApp WABA subscribed for webhooks: waba_id=%s", waba_id)
        return True
    except Exception as e:
        logger.warning("WhatsApp WABA subscribe error: waba_id=%s error=%s", waba_id, e)
        return False


def initiate_smb_app_data_sync(access_token: str, phone_number_id: str) -> dict[str, Any]:
    """
    Start contacts + history sync within Meta's 24h coexistence window.
    Returns {contacts: {...}|None, history: {...}|None, errors: [str]}.
    """
    out: dict[str, Any] = {"contacts": None, "history": None, "errors": []}
    phone_number_id = (phone_number_id or "").strip()
    token = (access_token or "").strip()
    if not phone_number_id or not token:
        out["errors"].append("missing phone_number_id or access_token")
        return out

    url = f"{META_GRAPH_API_BASE_URL}/{phone_number_id}/smb_app_data"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for sync_type, key in (
        ("smb_app_state_sync", "contacts"),
        ("history", "history"),
    ):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json={"messaging_product": "whatsapp", "sync_type": sync_type},
                timeout=30,
            )
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = {"raw": (resp.text or "")[:500]}
            if resp.status_code >= 400:
                msg = f"{sync_type} failed status={resp.status_code} body={body}"
                logger.warning(
                    "WhatsApp SMB sync failed: phone_number_id=%s sync_type=%s status=%s",
                    phone_number_id,
                    sync_type,
                    resp.status_code,
                )
                out["errors"].append(msg)
            else:
                out[key] = body
                logger.info(
                    "WhatsApp SMB sync accepted: phone_number_id=%s sync_type=%s request_id=%s",
                    phone_number_id,
                    sync_type,
                    (body or {}).get("request_id") if isinstance(body, dict) else None,
                )
        except Exception as e:
            msg = f"{sync_type} error: {e}"
            logger.warning(
                "WhatsApp SMB sync error: phone_number_id=%s sync_type=%s error=%s",
                phone_number_id,
                sync_type,
                e,
            )
            out["errors"].append(msg)
    return out


def verify_coexistence_phone(access_token: str, phone_number_id: str) -> Optional[dict]:
    """GET phone number fields is_on_biz_app, platform_type."""
    phone_number_id = (phone_number_id or "").strip()
    token = (access_token or "").strip()
    if not phone_number_id or not token:
        return None
    try:
        resp = requests.get(
            f"{META_GRAPH_API_BASE_URL}/{phone_number_id}",
            params={
                "access_token": token,
                "fields": "is_on_biz_app,platform_type,display_phone_number",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "verify_coexistence_phone failed: status=%s body=%s",
                resp.status_code,
                (resp.text or "")[:300],
            )
            return None
        return resp.json()
    except Exception as e:
        logger.debug("verify_coexistence_phone error: %s", e)
        return None


def extract_whatsapp_message_body(message: dict) -> str:
    """Best-effort text body for Cloud API / history / echo message objects."""
    if not isinstance(message, dict):
        return ""
    msg_type = (message.get("type") or "").strip()
    if msg_type == "text":
        return (message.get("text") or {}).get("body") or ""
    if msg_type in ("image", "video", "document", "audio", "sticker"):
        media = message.get(msg_type) or {}
        caption = (media.get("caption") or "").strip()
        if caption:
            return caption
        return f"[{msg_type} message]"
    if msg_type == "location":
        loc = message.get("location") or {}
        name = loc.get("name") or loc.get("address") or ""
        return name or "[location message]"
    if msg_type == "contacts":
        return "[contacts message]"
    if msg_type == "interactive":
        return "[interactive message]"
    if msg_type == "button":
        return (message.get("button") or {}).get("text") or "[button message]"
    if msg_type == "reaction":
        emoji = (message.get("reaction") or {}).get("emoji") or ""
        return f"[reaction {emoji}]".strip() if emoji else "[reaction]"
    if msg_type == "media_placeholder":
        return "[media message]"
    if msg_type:
        return f"[{msg_type} message]"
    return ""


def digits_only(value: Optional[str]) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())
