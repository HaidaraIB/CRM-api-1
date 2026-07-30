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
    Without this, outbound may work while inbound replies never reach the CRM.
    """
    waba_id = (waba_id or "").strip()
    token = (access_token or "").strip()
    if not waba_id or not token:
        return False
    url = f"{META_GRAPH_API_BASE_URL}/{waba_id}/subscribed_apps"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
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


def fetch_waba_subscribed_apps(access_token: str, waba_id: str) -> dict[str, Any]:
    """GET /{waba-id}/subscribed_apps — Meta sometimes returns 500 here even when POST succeeded."""
    out: dict[str, Any] = {"ok": False, "status_code": None, "body": None, "error": None}
    waba_id = (waba_id or "").strip()
    token = (access_token or "").strip()
    if not waba_id or not token:
        out["error"] = "missing waba_id or access_token"
        return out
    url = f"{META_GRAPH_API_BASE_URL}/{waba_id}/subscribed_apps"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        out["status_code"] = resp.status_code
        try:
            out["body"] = resp.json()
        except Exception:
            out["body"] = {"raw": (resp.text or "")[:500]}
        out["ok"] = resp.status_code < 400
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def fetch_phone_registration_fields(access_token: str, phone_number_id: str) -> Optional[dict]:
    """Phone fields used to decide Cloud register vs coexistence."""
    phone_number_id = (phone_number_id or "").strip()
    token = (access_token or "").strip()
    if not phone_number_id or not token:
        return None
    try:
        resp = requests.get(
            f"{META_GRAPH_API_BASE_URL}/{phone_number_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "fields": (
                    "id,display_phone_number,account_mode,is_on_biz_app,"
                    "platform_type,status,name_status,code_verification_status"
                ),
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(
                "fetch_phone_registration_fields failed: phone_number_id=%s status=%s body=%s",
                phone_number_id,
                resp.status_code,
                (resp.text or "")[:300],
            )
            return None
        return resp.json()
    except Exception as e:
        logger.warning("fetch_phone_registration_fields error: %s", e)
        return None


def register_cloud_phone_number(
    access_token: str,
    phone_number_id: str,
    pin: Optional[str] = None,
) -> dict[str, Any]:
    """
    POST /{phone_number_id}/register — required for Cloud API (non-coexistence) numbers.
    Skipping this causes Graph error 133010 Account not registered.
    Do NOT call for coexistence (is_on_biz_app) numbers.
    """
    import secrets

    out: dict[str, Any] = {
        "ok": False,
        "status_code": None,
        "body": None,
        "pin": None,
        "error": None,
        "skipped": False,
    }
    phone_number_id = (phone_number_id or "").strip()
    token = (access_token or "").strip()
    if not phone_number_id or not token:
        out["error"] = "missing phone_number_id or access_token"
        return out

    pin_digits = "".join(ch for ch in str(pin or "") if ch.isdigit())
    if len(pin_digits) != 6:
        pin_digits = f"{secrets.randbelow(1_000_000):06d}"
    out["pin"] = pin_digits

    url = f"{META_GRAPH_API_BASE_URL}/{phone_number_id}/register"
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"messaging_product": "whatsapp", "pin": pin_digits},
            timeout=60,
        )
        out["status_code"] = resp.status_code
        try:
            out["body"] = resp.json()
        except Exception:
            out["body"] = {"raw": (resp.text or "")[:500]}
        if resp.status_code < 400 and isinstance(out["body"], dict) and out["body"].get("success"):
            out["ok"] = True
            logger.info(
                "WhatsApp Cloud register ok: phone_number_id=%s",
                phone_number_id,
            )
        else:
            out["error"] = f"register failed status={resp.status_code} body={out['body']}"
            logger.warning(
                "WhatsApp Cloud register failed: phone_number_id=%s status=%s body=%s",
                phone_number_id,
                resp.status_code,
                str(out["body"])[:500],
            )
        return out
    except Exception as e:
        out["error"] = str(e)
        logger.warning(
            "WhatsApp Cloud register error: phone_number_id=%s error=%s",
            phone_number_id,
            e,
        )
        return out


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
