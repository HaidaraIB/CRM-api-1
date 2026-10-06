"""
Fetch Instagram / Messenger sender profiles from Meta Graph.

Webhooks carry only the app-scoped sender id (IGSID / PSID). Real names come
from a one-shot User Profile lookup using the Page access token.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from ..models import SocialChannel, SocialContact
from ..oauth_utils import MetaInboxOAuth

logger = logging.getLogger(__name__)

# Messaging User Profile API fields only — unknown fields (#100) fail the whole lookup.
_PROFILE_FIELDS = {
    SocialChannel.INSTAGRAM: 'name,username,profile_pic',
    SocialChannel.MESSENGER: 'first_name,last_name,profile_pic',
}

_PROFILE_PIC_RETRY = timedelta(hours=6)


def absolute_profile_pic_url(url: str) -> str:
    """FileSystemStorage.url is /media/... — browsers need the API host."""
    raw = (url or '').strip()
    if not raw or raw.startswith('http://') or raw.startswith('https://'):
        return raw
    from django.conf import settings

    base = (getattr(settings, 'API_BASE_URL', '') or '').rstrip('/')
    if not base:
        return raw
    path = raw if raw.startswith('/') else f'/{raw}'
    return f'{base}{path}'


def _extract_profile_pic_url(payload: dict) -> str:
    for key in ('profile_pic', 'profile_picture_url', 'profile_picture'):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            nested = raw.get('data') if isinstance(raw.get('data'), dict) else raw
            url = (nested or {}).get('url') or raw.get('url')
            if isinstance(url, str) and url.strip():
                return url.strip()
    return ''


def _parse_profile(channel: str, payload: dict) -> dict[str, str]:
    if not isinstance(payload, dict) or payload.get('error'):
        return {}

    pic = _extract_profile_pic_url(payload)

    if channel == SocialChannel.INSTAGRAM:
        return {
            'name': str(payload.get('name') or '').strip(),
            'username': str(payload.get('username') or '').strip().lstrip('@'),
            'profile_pic_url': pic,
        }

    first = str(payload.get('first_name') or '').strip()
    last = str(payload.get('last_name') or '').strip()
    full = ' '.join(part for part in (first, last) if part).strip()
    if not full:
        full = str(payload.get('name') or '').strip()
    return {
        'name': full,
        'username': '',
        'profile_pic_url': pic,
    }


def _store_profile_pic(contact: SocialContact, url: str) -> str:
    """Copy a Meta CDN avatar into our storage. Meta URLs expire."""
    if not url or url.startswith("/"):
        return absolute_profile_pic_url(url)
    try:
        response = requests.get(url, timeout=15)
    except requests.RequestException:
        logger.info("Meta Inbox: avatar download failed contact=%s", contact.id)
        return ""
    if not response.ok or not response.content:
        return ""
    key = f"social_profiles/{contact.company_id}/{contact.id}.jpg"
    if default_storage.exists(key):
        default_storage.delete(key)
    saved = default_storage.save(key, ContentFile(response.content))
    try:
        stored = default_storage.url(saved)
    except Exception:
        stored = saved
    return absolute_profile_pic_url(stored)


def fetch_contact_profile(contact: SocialContact, *, page_token: str) -> dict[str, str]:
    """Call Graph for one sender. Returns parsed profile fields (may be empty)."""
    fields = _PROFILE_FIELDS.get(contact.channel)
    if not fields or not contact.external_id:
        return {}

    handler = MetaInboxOAuth()
    params = {'fields': fields, 'access_token': page_token}
    proof = handler._appsecret_proof(page_token)
    if proof:
        params['appsecret_proof'] = proof

    url = f"{handler.graph_api_url}/{contact.external_id}"
    try:
        response = requests.get(url, params=params, timeout=15)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Meta Inbox: profile lookup failed for contact=%s: %s",
            contact.id,
            exc,
        )
        return {}

    if not response.ok:
        err = payload.get('error') if isinstance(payload, dict) else {}
        logger.info(
            "Meta Inbox: profile lookup rejected for contact=%s channel=%s code=%s subcode=%s message=%s",
            contact.id,
            contact.channel,
            (err or {}).get('code'),
            (err or {}).get('error_subcode'),
            (err or {}).get('message'),
        )
        return {}

    return _parse_profile(contact.channel, payload)


def ensure_contact_profile(contact: SocialContact, connection, *, force: bool = False) -> None:
    """
    Best-effort profile enrichment on first sight of a sender.

    Never raises — webhook ingestion must stay resilient.
    """
    if contact.profile_fetched_at and contact.profile_pic_url:
        return
    if contact.profile_fetched_at and not force:
        if contact.profile_pic_url:
            return
        if timezone.now() - contact.profile_fetched_at < _PROFILE_PIC_RETRY:
            return

    page_token = connection.get_page_access_token()
    now = timezone.now()
    if not page_token:
        return

    parsed = fetch_contact_profile(contact, page_token=page_token)
    pic = parsed.get('profile_pic_url', '')
    if pic:
        stored = _store_profile_pic(contact, pic)
        if stored:
            parsed['profile_pic_url'] = stored

    got_anything = any(parsed.get(f) for f in ('name', 'username', 'profile_pic_url'))
    # Empty Graph results must not stamp profile_fetched_at — that froze retries.
    if not got_anything:
        return

    update_fields = ['profile_fetched_at', 'updated_at']
    contact.profile_fetched_at = now
    for field in ('name', 'username', 'profile_pic_url'):
        value = parsed.get(field, '')
        if value and getattr(contact, field) != value:
            setattr(contact, field, value)
            update_fields.append(field)

    contact.save(update_fields=update_fields)


def refresh_profiles_for_conversations(conversations, *, limit: int = 10) -> None:
    """Best-effort: fill missing names/avatars for the visible inbox page."""
    refreshed = 0
    for conversation in conversations:
        if refreshed >= limit:
            break
        contact = getattr(conversation, 'contact', None)
        if contact is None or contact.channel == SocialChannel.WHATSAPP:
            continue
        missing_name = not (contact.name or '').strip()
        missing_pic = not (contact.profile_pic_url or '').strip()
        if not missing_name and not missing_pic:
            continue
        connection = getattr(conversation, 'connection', None)
        if connection is None:
            continue
        try:
            ensure_contact_profile(contact, connection, force=True)
            contact.refresh_from_db()
            refreshed += 1
        except Exception:
            logger.exception(
                'Meta Inbox: list profile refresh failed contact=%s',
                contact.id,
            )
