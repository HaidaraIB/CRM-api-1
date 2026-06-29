"""OTPIQ SMS API client for per-company outbound messages."""
from __future__ import annotations

import logging
import re
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

SENDER_ID_STATUS_ACCEPTED = 'accepted'


def mask_phone_for_log(phone: str) -> str:
    digits = re.sub(r'\D', '', phone or '')
    if len(digits) <= 4:
        return '****'
    return f"***{digits[-4:]}"


def body_preview_for_log(body: str, max_len: int = 80) -> str:
    text = (body or '').replace('\n', ' ').strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…"


def normalize_phone_for_otpiq(phone: str) -> str:
    """OTPIQ expects digits only without + (e.g. 964750123456)."""
    e164 = normalize_phone_to_e164(phone)
    return e164.lstrip('+')


def otpiq_api_base_url() -> str:
    return (getattr(settings, 'OTPIQ_API_BASE_URL', None) or 'https://api.otpiq.com/api').rstrip('/')


def _otpiq_auth_headers(api_key: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


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


def fetch_sender_ids(api_key: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """
    Retrieve sender IDs for the OTPIQ project.

    Returns (items, error_message). items is None on transport/API failure.
    """
    api_key = (api_key or '').strip()
    if not api_key:
        return None, 'OTPIQ API key is required.'

    url = f'{otpiq_api_base_url()}/sender-ids'
    try:
        resp = requests.get(url, headers=_otpiq_auth_headers(api_key), timeout=30)
    except requests.RequestException as e:
        logger.warning('OTPIQ sender-ids request failed: %s', e)
        return None, str(e) or 'Failed to load sender IDs.'

    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}

    if resp.status_code == 200 and isinstance(data, dict):
        raw = data.get('data')
        if isinstance(raw, list):
            return raw, None
        return [], None

    error_msg = ''
    if isinstance(data, dict):
        error_msg = data.get('error') or data.get('message') or ''
    if not error_msg:
        error_msg = resp.text[:400] if resp.text else 'Failed to load sender IDs.'
    return None, error_msg


def get_sender_id_status(api_key: str, sender_id: str) -> str | None:
    """
    Return OTPIQ status for sender_id (pending|accepted|rejected), or None if unknown.
    """
    sender = (sender_id or '').strip()
    if not sender:
        return None

    items, err = fetch_sender_ids(api_key)
    if items is None:
        logger.warning('OTPIQ sender ID lookup failed for %r: %s', sender, err)
        return None

    sender_lower = sender.lower()
    for row in items:
        if not isinstance(row, dict):
            continue
        sid = (row.get('senderId') or '').strip()
        if sid.lower() == sender_lower:
            return (row.get('status') or '').strip().lower() or None
    return None


def validate_sender_id_for_send(api_key: str, sender_id: str | None) -> tuple[bool, str | None, str | None]:
    """
    Block send when a configured sender ID is not accepted by OTPIQ.

    Returns (ok, error_key, error_message).
    """
    sender = (sender_id or '').strip()
    if not sender:
        return True, None, None

    status = get_sender_id_status(api_key, sender)
    if status == SENDER_ID_STATUS_ACCEPTED:
        return True, None, None
    if status in ('pending', 'rejected'):
        label = 'pending approval' if status == 'pending' else 'rejected'
        return (
            False,
            'sms_error_sender_id_not_approved',
            f'Sender ID "{sender}" is {label} in OTPIQ. Wait for approval or clear Sender ID in Integrations → SMS.',
        )
    if status is None:
        return (
            False,
            'sms_error_sender_id_not_approved',
            f'Sender ID "{sender}" is not registered in your OTPIQ project. Register it in OTPIQ or clear Sender ID in Integrations → SMS.',
        )
    return True, None, None


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

    logger.info(
        'OTPIQ SMS outbound to=%s body_len=%s body_preview=%r sender_id=%s route=%s',
        mask_phone_for_log(phone_digits),
        len(body or ''),
        body_preview_for_log(body),
        sender or '(none)',
        route,
    )

    url = f'{otpiq_api_base_url()}/sms'
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=_otpiq_auth_headers(api_key),
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
        logger.info(
            'OTPIQ SMS accepted sms_id=%s to=%s',
            sms_id,
            mask_phone_for_log(phone_digits),
        )
        return True, sms_id, None, None

    error_msg = ''
    if isinstance(data, dict):
        error_msg = data.get('error') or data.get('message') or ''
    if not error_msg:
        error_msg = resp.text[:400] if resp.text else 'SMS request was rejected.'
    error_key = otpiq_error_to_key(resp.status_code, data if isinstance(data, dict) else None)
    logger.warning(
        'OTPIQ SMS rejected status=%s key=%s to=%s',
        resp.status_code,
        error_key,
        mask_phone_for_log(phone_digits),
    )
    return False, None, error_key, error_msg
