"""
Outbound sending for the Omni-Channel Inbox.

Both channels send through the PAGE endpoint — POST /{page_id}/messages with a
page access token — including Instagram DMs. There is no separate IG send host
on the Facebook Login path.

The messaging window is stricter than WhatsApp's: there are no approved templates
to reopen a closed thread, only the HUMAN_AGENT tag (7 days) and only once Meta
has approved the Human Agent feature. Sending outside the window is refused
locally before any Graph call, because abusing it risks app-level enforcement.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from ..oauth_utils import MetaInboxOAuth

logger = logging.getLogger(__name__)

STANDARD_WINDOW = timedelta(hours=24)
HUMAN_AGENT_WINDOW = timedelta(days=7)

MODE_RESPONSE = 'response'
MODE_HUMAN_AGENT = 'human_agent'
MODE_CLOSED = 'closed'

# Meta Graph error code → CRM error_key. error_subcode is checked first because
# it is the specific reason; code alone is often just "permissions".
_ERROR_SUBCODES = {
    2534022: 'social_permission_denied',
    2018278: 'social_outside_window',
}
_ERROR_CODES = {
    10: 'social_permission_denied',
    100: 'social_invalid_recipient',
    190: 'social_token_invalid',
    200: 'social_permission_denied',
    368: 'social_temporarily_blocked',
    551: 'social_user_unavailable',
    613: 'social_rate_limited',
    2018001: 'social_no_matching_user',
    2018108: 'social_outside_window',
    2018278: 'social_outside_window',
}


class SendWindowClosed(Exception):
    """Raised when the reply window has elapsed. Never reaches Graph."""

    error_key = 'social_outside_window'


def human_agent_enabled() -> bool:
    return bool(getattr(settings, 'META_INBOX_HUMAN_AGENT_TAG_ENABLED', False))


def describe_window(conversation) -> dict:
    """
    The composer's contract: whether a reply is allowed and under which rule.

    Mirrors what whatsapp_session_window provides for WhatsApp so the frontend
    can disable the input and show the right hint instead of letting an agent
    type a message that will be rejected.
    """
    last_inbound = getattr(conversation, 'last_inbound_at', None)
    if not last_inbound:
        return {
            'open': False,
            'mode': MODE_CLOSED,
            'last_inbound_at': None,
            'expires_at': None,
            'human_agent_available': human_agent_enabled(),
        }

    now = timezone.now()
    delta = now - last_inbound

    if delta <= STANDARD_WINDOW:
        return {
            'open': True,
            'mode': MODE_RESPONSE,
            'last_inbound_at': last_inbound,
            'expires_at': last_inbound + STANDARD_WINDOW,
            'human_agent_available': human_agent_enabled(),
        }

    if delta <= HUMAN_AGENT_WINDOW and human_agent_enabled():
        return {
            'open': True,
            'mode': MODE_HUMAN_AGENT,
            'last_inbound_at': last_inbound,
            'expires_at': last_inbound + HUMAN_AGENT_WINDOW,
            'human_agent_available': True,
        }

    return {
        'open': False,
        'mode': MODE_CLOSED,
        'last_inbound_at': last_inbound,
        'expires_at': last_inbound + STANDARD_WINDOW,
        'human_agent_available': human_agent_enabled(),
    }


def resolve_messaging_type(conversation) -> tuple[str, str | None]:
    """Returns (messaging_type, tag). Raises SendWindowClosed when not allowed."""
    window = describe_window(conversation)
    if window['mode'] == MODE_RESPONSE:
        return 'RESPONSE', None
    if window['mode'] == MODE_HUMAN_AGENT:
        return 'MESSAGE_TAG', 'HUMAN_AGENT'
    raise SendWindowClosed()


def error_key_from_graph(payload) -> str:
    if not isinstance(payload, dict):
        return 'social_send_failed'
    err = payload.get('error')
    if not isinstance(err, dict):
        return 'social_send_failed'

    for value, table in ((err.get('error_subcode'), _ERROR_SUBCODES),
                         (err.get('code'), _ERROR_CODES)):
        try:
            as_int = int(value) if value is not None else None
        except (TypeError, ValueError):
            as_int = None
        if as_int is not None and as_int in table:
            return table[as_int]
    return 'social_send_failed'


def graph_error_message(payload) -> str:
    if isinstance(payload, dict) and isinstance(payload.get('error'), dict):
        err = payload['error']
        message = err.get('message') or 'Meta rejected the message.'
        code = err.get('code')
        return f"({code}) {message}" if code else message
    return 'Meta rejected the message.'


def _send_url(handler, page_id: str) -> str:
    return f"{handler.graph_api_url}/{page_id}/messages"


def send_message(
    conversation,
    *,
    text: str | None = None,
    attachment_type: str | None = None,
    attachment_file=None,
) -> dict:
    """
    Deliver a message through Graph.

    Returns {'ok': bool, 'mid': str|None, 'error_key': str|None,
             'error_message': str|None, 'raw': dict}.

    Does not raise on Graph failure — callers persist the failure onto the
    message row so the agent sees a failed bubble with a retry affordance.
    """
    connection = conversation.connection
    page_token = connection.get_page_access_token()
    if not page_token:
        return {
            'ok': False,
            'mid': None,
            'error_key': 'meta_inbox_page_token_missing',
            'error_message': 'This Page is not connected. Reconnect it in Integrations.',
            'raw': {},
        }

    messaging_type, tag = resolve_messaging_type(conversation)

    handler = MetaInboxOAuth()
    url = _send_url(handler, connection.page_id)
    params = {'access_token': page_token}
    proof = handler._appsecret_proof(page_token)
    if proof:
        params['appsecret_proof'] = proof

    recipient_id = conversation.contact.external_id

    try:
        if attachment_file is not None:
            # Multipart upload rather than a public URL, so our media never has
            # to be exposed to the internet for Meta to fetch it.
            data = {
                'recipient': f'{{"id":"{recipient_id}"}}',
                'message': (
                    f'{{"attachment":{{"type":"{attachment_type or "file"}",'
                    f'"payload":{{"is_reusable":false}}}}}}'
                ),
                'messaging_type': messaging_type,
            }
            if tag:
                data['tag'] = tag
            response = requests.post(
                url,
                params=params,
                data=data,
                files={'filedata': (attachment_file.name, attachment_file, getattr(attachment_file, 'content_type', None))},
                timeout=60,
            )
        else:
            body = {
                'recipient': {'id': recipient_id},
                'message': {'text': text or ''},
                'messaging_type': messaging_type,
            }
            if tag:
                body['tag'] = tag
            response = requests.post(url, params=params, json=body, timeout=30)
    except requests.RequestException as exc:
        logger.warning("Meta Inbox send failed (network) conv=%s: %s", conversation.id, exc)
        return {
            'ok': False,
            'mid': None,
            'error_key': 'social_send_failed',
            'error_message': 'Could not reach Meta. Please retry.',
            'raw': {},
        }

    try:
        payload = response.json()
    except ValueError:
        payload = {'error': {'message': response.text or 'Invalid response from Meta'}}

    if response.ok and not payload.get('error'):
        return {
            'ok': True,
            'mid': str(payload.get('message_id') or ''),
            'error_key': None,
            'error_message': None,
            'raw': payload,
        }

    error_key = error_key_from_graph(payload)

    # A dead token means every future send fails silently until someone
    # reconnects, so surface it on the connection rather than only on the bubble.
    if error_key == 'social_token_invalid':
        connection.status = 'error'
        connection.error_message = (
            'The Page access token is no longer valid. Reconnect this Page to resume messaging.'
        )
        connection.save(update_fields=['status', 'error_message', 'updated_at'])

    return {
        'ok': False,
        'mid': None,
        'error_key': error_key,
        'error_message': graph_error_message(payload),
        'raw': payload,
    }
