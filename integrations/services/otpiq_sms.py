"""OTPIQ SMS API client for per-company outbound messages."""
from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from integrations.services.twilio_phone import normalize_phone_to_e164

logger = logging.getLogger(__name__)

OTPIQ_ROUTE_PROVIDERS = frozenset({
    'auto',
    'whatsapp-sms',
    'telegram-sms',
    'whatsapp-telegram-sms',
    'sms',
    'whatsapp',
    'telegram',
})


def normalize_phone_for_otpiq(phone: str) -> str:
    """OTPIQ expects digits only without + (e.g. 964750123456)."""
    e164 = normalize_phone_to_e164(phone)
    return e164.lstrip('+')


def otpiq_api_base_url() -> str:
    return (getattr(settings, 'OTPIQ_API_BASE_URL', None) or 'https://api.otpiq.com/api').rstrip('/')


def otpiq_error_to_key(status_code: int | None, body: dict[str, Any] | None) -> str:
    msg = ''
    if body:
        msg = (body.get('error') or body.get('message') or '').lower()
    if status_code == 401 or 'unauthorized' in msg:
        return 'sms_error_auth'
    if status_code == 429 or 'rate limit' in msg:
        return 'sms_error_rate_limit'
    if 'insufficient credit' in msg:
        return 'sms_error_insufficient_credit'
    if 'senderid' in msg.replace(' ', ''):
        return 'sms_error_invalid_from_number'
    if 'phone' in msg and ('valid' in msg or 'invalid' in msg):
        return 'sms_error_invalid_to_number'
    if 'trial mode' in msg:
        return 'sms_error_permission'
    if status_code and 400 <= status_code < 500:
        return 'sms_error_validation'
    return 'sms_error_send_failed'


def send_custom_message(
    *,
    api_key: str,
    phone: str,
    body: str,
    sender_id: str | None = None,
    route_provider: str = 'sms',
) -> tuple[bool, str | None, str | None, str | None]:
    """
    Send a custom SMS via OTPIQ.

    Returns (success, sms_id, error_key, error_message).
    """
    api_key = (api_key or '').strip()
    if not api_key:
        return False, None, 'sms_error_credentials_incomplete', 'OTPIQ API key is required.'

    phone_digits = normalize_phone_for_otpiq(phone)
    if not phone_digits.isdigit() or len(phone_digits) < 10:
        return False, None, 'sms_error_invalid_to_number', 'Invalid phone number.'

    route = (route_provider or 'sms').strip() or 'sms'
    if route not in OTPIQ_ROUTE_PROVIDERS:
        route = 'sms'

    payload: dict[str, Any] = {
        'phoneNumber': phone_digits,
        'smsType': 'custom',
        'customMessage': body,
        'provider': route,
    }
    sender = (sender_id or '').strip()
    if sender:
        payload['senderId'] = sender[:11]

    url = f'{otpiq_api_base_url()}/sms'
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
    except requests.RequestException as e:
        logger.warning('OTPIQ request failed: %s', e)
        return False, None, 'sms_error_send_failed', str(e) or 'Failed to send SMS.'

    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}

    if resp.status_code == 200:
        sms_id = data.get('smsId') if isinstance(data, dict) else None
        return True, sms_id, None, None

    error_msg = ''
    if isinstance(data, dict):
        error_msg = data.get('error') or data.get('message') or ''
    if not error_msg:
        error_msg = resp.text[:400] if resp.text else 'SMS request was rejected.'
    error_key = otpiq_error_to_key(resp.status_code, data if isinstance(data, dict) else None)
    return False, None, error_key, error_msg
