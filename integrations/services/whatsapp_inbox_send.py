"""
Outbound WhatsApp Cloud API sends for Omni-Channel Inbox conversations.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.utils import timezone

from integrations.oauth_utils import META_GRAPH_API_BASE_URL

logger = logging.getLogger(__name__)

STANDARD_WINDOW = timedelta(hours=24)


def describe_whatsapp_window(conversation) -> dict:
    last_inbound = getattr(conversation, 'last_inbound_at', None)
    if not last_inbound:
        return {
            'open': False,
            'mode': 'closed',
            'last_inbound_at': None,
            'expires_at': None,
            'requires_template': True,
            'human_agent_available': False,
        }
    now = timezone.now()
    if now - last_inbound <= STANDARD_WINDOW:
        return {
            'open': True,
            'mode': 'customer_care',
            'last_inbound_at': last_inbound,
            'expires_at': last_inbound + STANDARD_WINDOW,
            'requires_template': False,
            'human_agent_available': False,
        }
    return {
        'open': False,
        'mode': 'closed',
        'last_inbound_at': last_inbound,
        'expires_at': last_inbound + STANDARD_WINDOW,
        'requires_template': True,
        'human_agent_available': False,
    }


class WhatsAppInboxWindowClosed(Exception):
    error_key = 'social_outside_window'


def send_whatsapp_inbox_message(
    conversation,
    *,
    text: str | None = None,
    attachment_type: str | None = None,
    attachment_file=None,
) -> dict:
    from ..models import SocialChannel

    if conversation.channel != SocialChannel.WHATSAPP:
        return {
            'ok': False,
            'mid': None,
            'error_key': 'bad_request',
            'error_message': 'Not a WhatsApp inbox conversation.',
            'raw': {},
        }

    window = describe_whatsapp_window(conversation)
    if not window['open']:
        return {
            'ok': False,
            'mid': None,
            'error_key': 'social_outside_window',
            'error_message': 'The 24-hour reply window has closed. Send an approved template.',
            'raw': {},
        }

    inbox_number = conversation.wa_inbox_number
    if not inbox_number or inbox_number.status != 'connected':
        return {
            'ok': False,
            'mid': None,
            'error_key': 'whatsapp_inbox_not_connected',
            'error_message': 'WhatsApp inbox number is not connected.',
            'raw': {},
        }

    token = inbox_number.get_access_token()
    if not token:
        return {
            'ok': False,
            'mid': None,
            'error_key': 'whatsapp_inbox_token_missing',
            'error_message': 'Reconnect the WhatsApp inbox number in Integrations.',
            'raw': {},
        }

    to = conversation.contact.external_id
    url = f"{META_GRAPH_API_BASE_URL}/{inbox_number.phone_number_id}/messages"
    headers = {'Authorization': f'Bearer {token}'}

    try:
        if attachment_file is not None:
            # Media upload via URL-less path is non-trivial; v1 uses document upload pattern.
            payload = {
                'messaging_product': 'whatsapp',
                'to': to,
                'type': attachment_type or 'document',
            }
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        else:
            payload = {
                'messaging_product': 'whatsapp',
                'to': to,
                'type': 'text',
                'text': {'body': text or ''},
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        logger.warning('WhatsApp inbox send network error conv=%s: %s', conversation.id, exc)
        return {
            'ok': False,
            'mid': None,
            'error_key': 'social_send_failed',
            'error_message': 'Could not reach Meta. Please retry.',
            'raw': {},
        }

    try:
        data = response.json()
    except ValueError:
        data = {'error': {'message': response.text}}

    if response.ok and not data.get('error'):
        messages = data.get('messages') or []
        mid = str((messages[0] or {}).get('id', '')) if messages else ''
        return {
            'ok': True,
            'mid': mid,
            'error_key': None,
            'error_message': None,
            'raw': data,
        }

    err = data.get('error') if isinstance(data.get('error'), dict) else {}
    return {
        'ok': False,
        'mid': None,
        'error_key': 'social_send_failed',
        'error_message': err.get('message') or 'WhatsApp rejected the message.',
        'raw': data,
    }
