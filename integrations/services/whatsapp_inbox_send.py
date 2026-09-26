"""
Outbound WhatsApp Cloud API sends for Omni-Channel Inbox conversations.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

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


def _graph_json_post(url: str, token: str, payload: dict, *, timeout: int = 30) -> tuple[bool, dict]:
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning('WhatsApp inbox Graph POST failed: %s', exc)
        return False, {'error': {'message': str(exc)}}
    try:
        data = response.json()
    except ValueError:
        data = {'error': {'message': response.text}}
    if response.ok and not data.get('error'):
        return True, data
    return False, data


def _send_result(ok: bool, data: dict) -> dict:
    if ok:
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


def _inbox_send_guard(conversation):
    from ..models import SocialChannel

    if conversation.channel != SocialChannel.WHATSAPP:
        return None, {
            'ok': False,
            'mid': None,
            'error_key': 'bad_request',
            'error_message': 'Not a WhatsApp inbox conversation.',
            'raw': {},
        }
    window = describe_whatsapp_window(conversation)
    if not window['open']:
        return None, {
            'ok': False,
            'mid': None,
            'error_key': 'social_outside_window',
            'error_message': 'The 24-hour reply window has closed. Send an approved template.',
            'raw': {},
        }
    inbox_number = conversation.wa_inbox_number
    if not inbox_number or inbox_number.status != 'connected':
        return None, {
            'ok': False,
            'mid': None,
            'error_key': 'whatsapp_inbox_not_connected',
            'error_message': 'WhatsApp inbox number is not connected.',
            'raw': {},
        }
    token = inbox_number.get_access_token()
    if not token:
        return None, {
            'ok': False,
            'mid': None,
            'error_key': 'whatsapp_inbox_token_missing',
            'error_message': 'Reconnect the WhatsApp inbox number in Integrations.',
            'raw': {},
        }
    to = conversation.contact.external_id
    url = f"{META_GRAPH_API_BASE_URL}/{inbox_number.phone_number_id}/messages"
    return (inbox_number, token, to, url), None


def send_whatsapp_inbox_message(
    conversation,
    *,
    text: str | None = None,
    attachment_type: str | None = None,
    attachment_file=None,
    is_voice_note: bool = False,
) -> dict:
    ctx, err = _inbox_send_guard(conversation)
    if err:
        return err
    inbox_number, token, to, url = ctx

    if attachment_file is not None:
        from integrations.services import whatsapp_media as wa_media

        file_bytes = attachment_file.read()
        filename = getattr(attachment_file, 'name', None) or 'file'
        mime = getattr(attachment_file, 'content_type', None) or ''
        kind = attachment_type or wa_media.KIND_DOCUMENT
        try:
            file_bytes, mime, filename, is_voice_note = wa_media.prepare_bytes_for_meta(
                data=file_bytes,
                mime=mime,
                filename=filename,
                is_voice_note=is_voice_note and kind == wa_media.KIND_AUDIO,
            )
        except ValueError as exc:
            return {
                'ok': False,
                'mid': None,
                'error_key': (
                    wa_media.VOICE_NOTE_CONVERT_ERROR_CODE
                    if wa_media.is_voice_note_convert_error(exc)
                    else 'bad_request'
                ),
                'error_message': str(exc),
                'raw': {},
            }
        try:
            meta_media_id = wa_media.upload_media_to_meta(
                phone_number_id=inbox_number.phone_number_id,
                access_token=token,
                data=file_bytes,
                mime=mime,
                filename=filename,
            )
        except ValueError as exc:
            return {
                'ok': False,
                'mid': None,
                'error_key': 'bad_request',
                'error_message': str(exc),
                'raw': {},
            }
        except requests.RequestException:
            return {
                'ok': False,
                'mid': None,
                'error_key': 'social_send_failed',
                'error_message': 'Could not upload media to Meta.',
                'raw': {},
            }
        media_part = wa_media.graph_media_payload(
            kind=kind,
            media_id=meta_media_id,
            caption=text,
            filename=filename,
            is_voice_note=is_voice_note,
        )
        payload = {
            'messaging_product': 'whatsapp',
            'recipient_type': 'individual',
            'to': to,
            'type': kind,
            kind: media_part,
        }
        ok, data = _graph_json_post(url, token, payload, timeout=60)
        return _send_result(ok, data)

    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': text or ''},
    }
    ok, data = _graph_json_post(url, token, payload)
    return _send_result(ok, data)


def send_whatsapp_inbox_location(
    conversation,
    *,
    latitude: Decimal,
    longitude: Decimal,
    name: str = '',
    address: str = '',
) -> dict:
    ctx, err = _inbox_send_guard(conversation)
    if err:
        return err
    _inbox_number, token, to, url = ctx
    location_obj = {
        'latitude': float(latitude),
        'longitude': float(longitude),
    }
    if name:
        location_obj['name'] = name[:255]
    if address:
        location_obj['address'] = address[:512]
    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to,
        'type': 'location',
        'location': location_obj,
    }
    ok, data = _graph_json_post(url, token, payload)
    return _send_result(ok, data)
