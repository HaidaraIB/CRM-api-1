"""Dispatch outbound company SMS to Twilio or OTPIQ."""
from __future__ import annotations

from typing import TYPE_CHECKING

from integrations.models import SmsProvider
from integrations.services import otpiq_sms
from integrations.services.twilio_phone import normalize_phone_to_e164
from integrations.services.twilio_text import strip_ansi, twilio_error_to_key

if TYPE_CHECKING:
    from integrations.models import TwilioSettings


def send_company_sms(
    settings: TwilioSettings,
    *,
    to_phone: str,
    body: str,
) -> tuple[bool, str | None, str | None, str | None, str]:
    """
    Send SMS using the company's configured provider.

    Returns (success, external_message_id, error_key, error_message, provider).
    provider is SmsProvider value (twilio|otpiq).
    """
    provider = (settings.provider or SmsProvider.TWILIO).strip()
    if provider == SmsProvider.OTPIQ:
        return _send_via_otpiq(settings, to_phone=to_phone, body=body)
    return _send_via_twilio(settings, to_phone=to_phone, body=body)


def _send_via_otpiq(
    settings: TwilioSettings,
    *,
    to_phone: str,
    body: str,
) -> tuple[bool, str | None, str | None, str | None, str]:
    api_key = settings.get_otpiq_api_key()
    sender_id = (settings.sender_id or '').strip() or None
    route = (settings.otpiq_route_provider or 'sms').strip() or 'sms'
    ok, sms_id, error_key, error_msg = otpiq_sms.send_custom_message(
        api_key=api_key or '',
        phone=to_phone,
        body=body,
        sender_id=sender_id,
        route_provider=route,
    )
    return ok, sms_id, error_key, error_msg, SmsProvider.OTPIQ


def _send_via_twilio(
    settings: TwilioSettings,
    *,
    to_phone: str,
    body: str,
) -> tuple[bool, str | None, str | None, str | None, str]:
    account_sid = settings.account_sid
    auth_token = settings.get_auth_token()
    twilio_number = settings.twilio_number
    sender_id = (settings.sender_id or '').strip()
    from_value = sender_id if sender_id else (twilio_number or '')
    if not account_sid or not auth_token or not from_value:
        return (
            False,
            None,
            'sms_error_credentials_incomplete',
            'Account SID, Auth Token, and either Sender ID or sender number are required.',
            SmsProvider.TWILIO,
        )

    try:
        from twilio.base.exceptions import TwilioRestException
        from twilio.rest import Client as TwilioClient

        twilio_client = TwilioClient(account_sid, auth_token)
        to = normalize_phone_to_e164(to_phone)
        message = twilio_client.messages.create(
            body=body,
            from_=from_value,
            to=to,
        )
        return True, message.sid, None, None, SmsProvider.TWILIO
    except TwilioRestException as e:
        error_key = twilio_error_to_key(e)
        clean_msg = strip_ansi(getattr(e, 'msg', None) or str(e))
        if clean_msg and len(clean_msg) > 400:
            clean_msg = clean_msg.split('\n')[0]
        return False, None, error_key, clean_msg or 'SMS request was rejected.', SmsProvider.TWILIO
    except Exception as e:
        clean_msg = strip_ansi(str(e))
        if len(clean_msg) > 400:
            clean_msg = clean_msg.split('\n')[0]
        return False, None, 'sms_error_send_failed', clean_msg or 'Failed to send SMS.', SmsProvider.TWILIO
