"""
WhatsApp Cloud Calling — agent Away status + business call hours.

- Away: per-user timed unavailability (15/30/60 min); hides inbound rings from that agent.
- Call hours: per WhatsAppAccount weekly schedule; synced to Meta call_hours when possible.
  Outside hours: reject inbound + send out_of_hours_message text (best-effort).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from integrations.models import WhatsAppAccount, WhatsAppCall, WhatsAppCallStatus

logger = logging.getLogger(__name__)

AWAY_DURATION_CHOICES = (15, 30, 60)

WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_META_DAY = {
    "monday": "MONDAY",
    "tuesday": "TUESDAY",
    "wednesday": "WEDNESDAY",
    "thursday": "THURSDAY",
    "friday": "FRIDAY",
    "saturday": "SATURDAY",
    "sunday": "SUNDAY",
}

DEFAULT_OUT_OF_HOURS_MESSAGE_EN = (
    "Thank you for calling. We are currently outside business hours. "
    "Please send us a message and we will get back to you soon."
)
DEFAULT_OUT_OF_HOURS_MESSAGE_AR = (
    "شكراً لاتصالك. نحن حالياً خارج ساعات العمل. "
    "يرجى إرسال رسالة وسنعاود التواصل معك قريباً."
)
# Backward-compatible alias (English).
DEFAULT_OUT_OF_HOURS_MESSAGE = DEFAULT_OUT_OF_HOURS_MESSAGE_EN


def default_out_of_hours_message(language: str | None = None) -> str:
    lang = (language or "en").strip().lower()
    if lang.startswith("ar"):
        return DEFAULT_OUT_OF_HOURS_MESSAGE_AR
    return DEFAULT_OUT_OF_HOURS_MESSAGE_EN


def out_of_hours_message_text(account: WhatsAppAccount, language: str | None = None) -> str:
    text = (account.out_of_hours_message or "").strip()
    if text:
        return text
    return default_out_of_hours_message(language)


def user_is_whatsapp_call_away(user) -> bool:
    until = getattr(user, "whatsapp_call_away_until", None)
    if not until:
        return False
    return until > timezone.now()


def serialize_agent_call_status(user) -> dict[str, Any]:
    away = user_is_whatsapp_call_away(user)
    until = getattr(user, "whatsapp_call_away_until", None) if away else None
    return {
        "status": "away" if away else "ready",
        "away_until": until.isoformat() if until else None,
        "away_durations_minutes": list(AWAY_DURATION_CHOICES),
    }


def set_agent_call_away(user, *, duration_minutes: Optional[int]) -> dict[str, Any]:
    """
    duration_minutes: 15/30/60 to go Away, or None/0 to return Ready.
    """
    if not duration_minutes:
        user.whatsapp_call_away_until = None
        user.save(update_fields=["whatsapp_call_away_until"])
        return serialize_agent_call_status(user)

    minutes = int(duration_minutes)
    if minutes not in AWAY_DURATION_CHOICES:
        raise ValueError(f"duration_minutes must be one of {AWAY_DURATION_CHOICES}")
    user.whatsapp_call_away_until = timezone.now() + timedelta(minutes=minutes)
    user.save(update_fields=["whatsapp_call_away_until"])
    return serialize_agent_call_status(user)


def default_weekly_schedule() -> dict:
    """All days closed until configured."""
    return {day: {"closed": True, "open": "09:00", "close": "17:00"} for day in WEEKDAY_KEYS}


def normalize_weekly_schedule(raw: Any) -> dict:
    base = default_weekly_schedule()
    if not isinstance(raw, dict):
        return base
    for day in WEEKDAY_KEYS:
        entry = raw.get(day) or raw.get(day[:3])
        if not isinstance(entry, dict):
            continue
        closed = bool(entry.get("closed"))
        open_t = _normalize_hhmm(entry.get("open") or entry.get("open_time") or "09:00")
        close_t = _normalize_hhmm(entry.get("close") or entry.get("close_time") or "17:00")
        base[day] = {"closed": closed, "open": open_t, "close": close_t}
    return base


def _normalize_hhmm(value: Any) -> str:
    s = str(value or "").strip()
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 3:
        digits = f"0{digits}"
    if len(digits) == 4:
        return f"{digits[:2]}:{digits[2:]}"
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return "09:00"


def _hhmm_to_meta(hhmm: str) -> str:
    return _normalize_hhmm(hhmm).replace(":", "")


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    parts = _normalize_hhmm(hhmm).split(":")
    return int(parts[0]), int(parts[1])


def resolve_call_hours_timezone(account: WhatsAppAccount) -> str:
    tz = (account.call_hours_timezone or "").strip()
    if tz:
        return tz
    company_tz = getattr(getattr(account, "company", None), "timezone", None) or ""
    return (company_tz or "UTC").strip() or "UTC"


def is_within_call_hours(account: WhatsAppAccount, *, when: Optional[datetime] = None) -> bool:
    """
    True if calls are allowed now.
    When call_hours_enabled is False → always True (24/7).
    """
    if not account.call_hours_enabled:
        return True
    weekly = normalize_weekly_schedule(account.call_hours_weekly)
    tz_name = resolve_call_hours_timezone(account)
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    now = when or timezone.now()
    if timezone.is_aware(now):
        local = now.astimezone(tz)
    else:
        local = timezone.make_aware(now, timezone.get_current_timezone()).astimezone(tz)

    day_key = WEEKDAY_KEYS[local.weekday()]
    entry = weekly.get(day_key) or {"closed": True}
    if entry.get("closed"):
        return False
    open_h, open_m = _parse_hhmm(entry.get("open") or "09:00")
    close_h, close_m = _parse_hhmm(entry.get("close") or "17:00")
    minutes = local.hour * 60 + local.minute
    open_min = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    if open_min == close_min:
        return False
    if open_min < close_min:
        return open_min <= minutes < close_min
    # Overnight window (e.g. 22:00–06:00)
    return minutes >= open_min or minutes < close_min


def build_meta_call_hours_payload(account: WhatsAppAccount) -> dict:
    weekly = normalize_weekly_schedule(account.call_hours_weekly)
    operating = []
    for day in WEEKDAY_KEYS:
        entry = weekly[day]
        if entry.get("closed"):
            continue
        operating.append(
            {
                "day_of_week": _META_DAY[day],
                "open_time": _hhmm_to_meta(entry["open"]),
                "close_time": _hhmm_to_meta(entry["close"]),
            }
        )
    status = "ENABLED" if account.call_hours_enabled and operating else "DISABLED"
    payload: dict[str, Any] = {
        "status": status,
        "timezone_id": resolve_call_hours_timezone(account),
    }
    if operating:
        payload["weekly_operating_hours"] = operating
    return payload


def sync_call_hours_to_meta(account: WhatsAppAccount) -> dict:
    from integrations.services.whatsapp_calling import (
        WhatsAppCallingError,
        _graph_post,
        is_seed_whatsapp_account,
    )

    if is_seed_whatsapp_account(account):
        return {"success": True, "seed": True, "call_hours": build_meta_call_hours_payload(account)}

    body = {"calling": {"call_hours": build_meta_call_hours_payload(account)}}
    # Meta requires weekly_operating_hours when ENABLED — if empty schedule, force DISABLED.
    ch = body["calling"]["call_hours"]
    if ch.get("status") == "ENABLED" and not ch.get("weekly_operating_hours"):
        ch["status"] = "DISABLED"
        ch.pop("weekly_operating_hours", None)
    try:
        return _graph_post(account, f"{account.phone_number_id}/settings", body)
    except WhatsAppCallingError:
        logger.exception("Failed to sync call_hours to Meta account=%s", account.id)
        raise


def send_plain_whatsapp_text(account: WhatsAppAccount, *, to: str, body: str) -> dict:
    from integrations.services.whatsapp_calling import _graph_post, is_seed_whatsapp_account

    text = (body or "").strip()[:4096]
    to_digits = "".join(c for c in (to or "") if c.isdigit())
    if not text or not to_digits:
        return {}
    if is_seed_whatsapp_account(account):
        return {"success": True, "seed": True}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_digits,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    return _graph_post(account, f"{account.phone_number_id}/messages", payload)


def reject_inbound_out_of_hours(call: WhatsAppCall) -> WhatsAppCall:
    """Reject ringing inbound call and notify customer with OOH message."""
    from integrations.services.whatsapp_calling import (
        WhatsAppCallingError,
        ensure_client_call_for_whatsapp_call,
        graph_call_action,
    )

    account = call.whatsapp_account
    if call.status not in (WhatsAppCallStatus.RINGING,):
        return call

    try:
        graph_call_action(account, action="reject", call_id=call.meta_call_id)
    except WhatsAppCallingError:
        logger.warning("OOH Graph reject failed call=%s", call.id, exc_info=True)

    try:
        lang = "ar"
        company = getattr(account, "company", None)
        if company is not None:
            from accounts.models import Role, User

            owner = (
                User.objects.filter(company=company, role=Role.ADMIN.value)
                .order_by("id")
                .first()
            )
            if owner and getattr(owner, "language", None):
                lang = owner.language
        send_plain_whatsapp_text(
            account,
            to=call.peer_phone,
            body=out_of_hours_message_text(account, lang),
        )
    except Exception:
        logger.exception("OOH message send failed call=%s", call.id)

    call.status = WhatsAppCallStatus.REJECTED
    if not call.ended_at:
        call.ended_at = timezone.now()
    call.error_message = "out_of_hours"
    call.save(update_fields=["status", "ended_at", "error_message", "updated_at"])
    try:
        from integrations.services.whatsapp_call_error_logs import log_whatsapp_call_error
        from integrations.models import WhatsAppCallErrorSource

        log_whatsapp_call_error(
            company=call.company,
            source=WhatsAppCallErrorSource.OUT_OF_HOURS.value,
            error_code="out_of_hours",
            error_message="out_of_hours",
            agent=call.agent,
            client=call.client,
            peer_phone=call.peer_phone,
            whatsapp_account=account,
            whatsapp_call=call,
        )
    except Exception:
        logger.exception("OOH error log failed call=%s", call.id)
    ensure_client_call_for_whatsapp_call(call)
    return call


def serialize_call_hours(account: WhatsAppAccount) -> dict[str, Any]:
    return {
        "whatsapp_account_id": account.id,
        "enabled": bool(account.call_hours_enabled),
        "timezone": resolve_call_hours_timezone(account),
        "weekly": normalize_weekly_schedule(account.call_hours_weekly),
        "out_of_hours_message": account.out_of_hours_message or "",
        "default_out_of_hours_message": DEFAULT_OUT_OF_HOURS_MESSAGE,
        "within_hours_now": is_within_call_hours(account),
    }
