"""
Live credential checks when platform or tenant SMS/email settings are saved.
Does not send OTPs or billable messages — only provider auth / config lookups.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import requests
from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

_TWILIO_SENDER_ID_RE = re.compile(r"^[\w]{1,11}$")


def _list_errors(errors: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: v for k, v in errors.items() if v}


def validate_twilio_credentials(
    *,
    account_sid: str | None,
    auth_token: str | None,
    twilio_number: str | None,
    sender_id: str | None,
    require_number_for_registration: bool = False,
) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    sid = (account_sid or "").strip()
    token = (auth_token or "").strip() if auth_token else ""
    number = (twilio_number or "").strip()
    sender = (sender_id or "").strip()

    if sender and not _TWILIO_SENDER_ID_RE.match(sender):
        errors["sender_id"] = ["Sender ID must be 1–11 letters or digits."]

    if require_number_for_registration and not number:
        errors["twilio_number"] = [
            "Twilio sender number is required while registration phone OTP uses Twilio SMS.",
        ]

    if not sid and not token and not number:
        return _list_errors(errors)

    if sid and not token:
        return _list_errors(errors)

    if token and not sid:
        errors["account_sid"] = ["Account SID is required when an Auth Token is set."]
        return _list_errors(errors)

    if not sid or not token:
        return _list_errors(errors)

    try:
        from twilio.base.exceptions import TwilioRestException
        from twilio.rest import Client as TwilioClient

        client = TwilioClient(sid, token)
        client.api.accounts(sid).fetch()
    except Exception as exc:
        logger.info("Twilio account verify failed: %s", exc)
        errors["auth_token"] = [
            "Twilio credentials could not be verified. Check Account SID and Auth Token.",
        ]
        return _list_errors(errors)

    if number and "auth_token" not in errors:
        try:
            from integrations.services.twilio_phone import normalize_phone_to_e164

            raw = number if number.startswith("+") else f"+{number.lstrip('+')}"
            e164 = normalize_phone_to_e164(raw)
            if not e164:
                errors["twilio_number"] = [
                    "Enter a valid E.164 phone number (e.g. +9647xxxxxxxx).",
                ]
            else:
                listed = client.incoming_phone_numbers.list(phone_number=e164, limit=1)
                if not listed:
                    errors["twilio_number"] = [
                        "This number was not found on your Twilio account.",
                    ]
        except Exception as exc:
            logger.info("Twilio number verify failed: %s", exc)
            errors["twilio_number"] = ["Could not verify this Twilio number."]

    return _list_errors(errors)


def validate_otpiq_credentials(
    *,
    api_key: str | None,
    sender_id: str | None,
) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    key = (api_key or "").strip()
    sender = (sender_id or "").strip()

    if not key:
        return errors

    from integrations.services.otpiq_sms import fetch_sender_ids, validate_sender_id_for_send

    items, err = fetch_sender_ids(key)
    if items is None:
        msg = err or "OTPIQ API key could not be verified."
        errors["api_key"] = [msg]
        return errors

    if sender:
        ok, _key, msg = validate_sender_id_for_send(key, sender)
        if not ok:
            errors["sender_id"] = [msg or "Sender ID is not accepted in your OTPIQ project."]

    return _list_errors(errors)


def validate_whatsapp_platform_credentials(
    *,
    phone_number_id: str | None,
    access_token: str | None,
    graph_api_version: str | None,
    otp_template_name: str | None,
) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    pid = (phone_number_id or "").strip()
    token = (access_token or "").strip() if access_token else ""
    ver = (graph_api_version or "v25.0").strip().lstrip("v") or "25.0"
    if not ver.startswith("v"):
        ver = f"v{ver}"
    tpl = (otp_template_name or "").strip()

    if tpl and (len(tpl) > 128 or not re.match(r"^[\w._-]+$", tpl)):
        errors["otp_template_name"] = [
            "Template name must be letters, numbers, dots, underscores, or hyphens (max 128).",
        ]

    if not pid and not token:
        return _list_errors(errors)

    if pid and not token:
        errors["access_token"] = ["Access token is required when a phone number ID is set."]
        return _list_errors(errors)

    if token and not pid:
        errors["phone_number_id"] = ["Phone number ID is required when an access token is set."]
        return _list_errors(errors)

    url = f"https://graph.facebook.com/{ver}/{pid}"
    try:
        resp = requests.get(
            url,
            params={"fields": "id"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        if resp.status_code >= 400:
            errors["access_token"] = [
                "WhatsApp credentials could not be verified with Meta. Check token and phone number ID.",
            ]
    except requests.RequestException as exc:
        logger.info("WhatsApp Graph verify failed: %s", exc)
        errors["access_token"] = ["Could not reach Meta to verify WhatsApp credentials."]

    return _list_errors(errors)


def validate_resend_outbound_email(
    *,
    is_active: bool,
    from_email: str | None,
) -> dict[str, list[str]]:
    if not is_active:
        return {}

    errors: dict[str, list[str]] = {}
    email = (from_email or "").strip()
    if not email or "@" not in email:
        errors["from_email"] = ["A valid from email is required when outbound email is enabled."]
        return errors

    api_key = (getattr(django_settings, "RESEND_API_KEY", None) or "").strip()
    if not api_key:
        errors["non_field_errors"] = [
            "RESEND_API_KEY is not set on the server. Add it to the environment before enabling email.",
        ]
        return errors

    domain = email.split("@", 1)[-1].lower()
    try:
        resp = requests.get(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code == 401:
            errors["non_field_errors"] = ["Resend API key on the server is invalid."]
            return errors
        if resp.status_code >= 400:
            errors["from_email"] = ["Could not verify sender domain with Resend."]
            return errors
        payload = resp.json() if resp.content else {}
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            errors["from_email"] = ["Could not verify sender domain with Resend."]
            return errors
        verified = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or "").strip().lower()
            status = (row.get("status") or "").strip().lower()
            if name == domain and status in ("verified", "success"):
                verified = True
                break
        if not verified:
            errors["from_email"] = [
                f'Domain "{domain}" is not verified in Resend. Verify it in Resend before enabling email.',
            ]
    except requests.RequestException as exc:
        logger.info("Resend domain verify failed: %s", exc)
        errors["from_email"] = ["Could not reach Resend to verify the sender domain."]

    return _list_errors(errors)


def merge_validation_errors(*parts: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in parts:
        for key, msgs in part.items():
            out.setdefault(key, []).extend(msgs)
    return out
