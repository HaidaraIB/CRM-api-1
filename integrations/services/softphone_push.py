"""VoIP wake-up pushes for embedded softphone incoming calls."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import jwt
from django.conf import settings
from django.core.cache import cache

from integrations.models import UserSoftphoneDevice
from notifications.services import NotificationService

logger = logging.getLogger(__name__)

_APNS_VOIP_HOST_PROD = "https://api.push.apple.com"
_APNS_VOIP_HOST_SANDBOX = "https://api.sandbox.push.apple.com"
_SOFTPHONE_PUSH_DEDUPE_TTL = 90
_APNS_VOIP_EXPIRATION_SECONDS = 30


def _apns_voip_configured() -> bool:
    return bool(
        getattr(settings, "APNS_VOIP_KEY_PATH", "")
        or getattr(settings, "APNS_VOIP_KEY_CONTENT", "")
    ) and bool(getattr(settings, "APNS_VOIP_KEY_ID", "")) and bool(
        getattr(settings, "APNS_VOIP_TEAM_ID", ""
    ))


def apns_voip_status() -> dict[str, Any]:
    """Return non-secret APNs VoIP configuration status for ops checks."""
    key_path = getattr(settings, "APNS_VOIP_KEY_PATH", "") or ""
    key_content = bool(getattr(settings, "APNS_VOIP_KEY_CONTENT", "") or "")
    key_id = bool(getattr(settings, "APNS_VOIP_KEY_ID", ""))
    team_id = bool(getattr(settings, "APNS_VOIP_TEAM_ID", ""))
    bundle_id = getattr(settings, "APNS_BUNDLE_ID", "com.loopcrm.mobile")
    use_sandbox = getattr(settings, "APNS_VOIP_USE_SANDBOX", False)
    key_available = key_content or bool(key_path)
    if key_path and not key_content:
        try:
            with open(key_path, encoding="utf-8"):
                key_available = True
        except OSError:
            key_available = False
    configured = key_available and key_id and team_id
    return {
        "configured": configured,
        "key_id_set": key_id,
        "team_id_set": team_id,
        "key_source": "content" if key_content else ("path" if key_path else "missing"),
        "key_readable": key_available,
        "bundle_id": bundle_id,
        "voip_topic": f"{bundle_id}.voip",
        "use_sandbox": use_sandbox,
        "apns_host": _APNS_VOIP_HOST_SANDBOX if use_sandbox else _APNS_VOIP_HOST_PROD,
    }


def _load_apns_voip_key() -> str:
    content = getattr(settings, "APNS_VOIP_KEY_CONTENT", "") or ""
    if content:
        return content
    path = getattr(settings, "APNS_VOIP_KEY_PATH", "") or ""
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return ""


def _apns_voip_jwt() -> str:
    key = _load_apns_voip_key()
    if not key:
        raise ValueError("APNS VoIP key not configured")
    return jwt.encode(
        {"iss": settings.APNS_VOIP_TEAM_ID, "iat": int(time.time())},
        key,
        algorithm="ES256",
        headers={"kid": settings.APNS_VOIP_KEY_ID},
    )


def _softphone_push_dedupe_key(user_id: int, call_id: int | None, call_uuid: str) -> str:
    if call_id:
        return f"softphone_push_sent:{user_id}:{call_id}"
    return f"softphone_push_sent:{user_id}:uuid:{call_uuid}"


def _claim_softphone_push(user_id: int, call_id: int | None, call_uuid: str) -> bool:
    """Return True if this push should be sent (first delivery for this call)."""
    key = _softphone_push_dedupe_key(user_id, call_id, call_uuid)
    return cache.add(key, 1, timeout=_SOFTPHONE_PUSH_DEDUPE_TTL)


def _delete_stale_voip_device(voip_token: str) -> None:
    deleted, _ = UserSoftphoneDevice.objects.filter(voip_token=voip_token).delete()
    if deleted:
        logger.info("Removed stale VoIP device token=%s...", voip_token[:12])


def send_apns_voip_push(voip_token: str, payload: dict[str, Any]) -> tuple[bool, int | None]:
    """Send iOS VoIP push via APNs HTTP/2. Returns (success, http_status)."""
    if not voip_token or not _apns_voip_configured():
        return False, None
    try:
        import httpx

        use_sandbox = getattr(settings, "APNS_VOIP_USE_SANDBOX", False)
        host = _APNS_VOIP_HOST_SANDBOX if use_sandbox else _APNS_VOIP_HOST_PROD
        bundle_id = getattr(settings, "APNS_BUNDLE_ID", "com.loopcrm.mobile")
        url = f"{host}/3/device/{voip_token}"
        token = _apns_voip_jwt()
        expiration = int(time.time()) + _APNS_VOIP_EXPIRATION_SECONDS
        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": f"{bundle_id}.voip",
            "apns-push-type": "voip",
            "apns-priority": "10",
            "apns-expiration": str(expiration),
            "content-type": "application/json",
        }
        with httpx.Client(http2=True, timeout=10.0) as client:
            resp = client.post(url, headers=headers, content=json.dumps(payload))
        if resp.status_code == 200:
            logger.info("APNs VoIP push sent token=%s...", voip_token[:12])
            return True, resp.status_code
        if resp.status_code in (400, 410):
            reason = (resp.text or "").lower()
            if resp.status_code == 410 or "baddevicetoken" in reason:
                _delete_stale_voip_device(voip_token)
        logger.warning("APNs VoIP push failed status=%s body=%s", resp.status_code, resp.text[:200])
        return False, resp.status_code
    except ImportError:
        logger.warning("httpx not installed; cannot send APNs VoIP push")
        return False, None
    except Exception as exc:
        logger.exception("APNs VoIP push error: %s", exc)
        return False, None


def send_softphone_incoming_push(
    user,
    *,
    caller: str,
    extension: str,
    call_uuid: str | None = None,
    client_name: str = "",
    lead_id: int | None = None,
    call_id: int | None = None,
) -> bool:
    """
    Wake mobile softphone for an inbound call.

    - iOS: APNs VoIP push to registered voip_token devices
    - Android: high-priority FCM data message (softphone_incoming_call)
    """
    call_uuid = call_uuid or str(uuid.uuid4())
    if not _claim_softphone_push(user.id, call_id, call_uuid):
        logger.info(
            "Skipping duplicate softphone push user=%s call_id=%s call_uuid=%s",
            user.id,
            call_id,
            call_uuid,
        )
        return False

    data = {
        "kind": "softphone_incoming_call",
        "call_uuid": call_uuid,
        "caller": caller,
        "extension": extension,
        "client_name": client_name or "",
        "lead_id": lead_id or "",
        "call_id": call_id or "",
        "handle": caller,
        "nameCaller": client_name or caller,
    }

    sent = False
    apns_status: int | None = None
    devices = UserSoftphoneDevice.objects.filter(user=user).exclude(
        fcm_token="", voip_token=""
    )
    for device in devices:
        if device.platform == "ios" and device.voip_token:
            ok, status = send_apns_voip_push(
                device.voip_token,
                {
                    "aps": {"content-available": 1},
                    **{k: str(v) for k, v in data.items()},
                },
            )
            apns_status = status
            if ok:
                sent = True
        elif device.fcm_token:
            # Android uses FCM via NotificationService with high priority
            pass

    # FCM for Android (and fallback): data-only high-priority message
    fcm_sent = NotificationService.send_softphone_call_push(
        user=user,
        title=client_name or caller,
        body=caller,
        data=data,
    )
    logger.info(
        "softphone_push_dispatch user_id=%s call_id=%s call_uuid=%s apns_sent=%s apns_status=%s fcm_sent=%s",
        user.id,
        call_id,
        call_uuid,
        sent,
        apns_status,
        fcm_sent,
    )
    return sent or fcm_sent
