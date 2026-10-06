"""
Fetch Instagram / Messenger sender profiles from Meta Graph.

Webhooks carry only the app-scoped sender id (IGSID / PSID). Real names come
from a one-shot User Profile lookup using the Page access token.

Public entry points:
  enrich_contact(channel, external_id, *, company=None, connection=None, force=False)
  ensure_contact_profile(contact, connection, *, force=False)
  schedule_ensure_contact_profile(contact, connection, *, force=False)
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from ..models import MetaInboxConnection, SocialChannel, SocialContact
from ..oauth_utils import MetaInboxOAuth

logger = logging.getLogger(__name__)

# Messaging User Profile API fields only — unknown fields (#100) fail the whole lookup.
_PROFILE_FIELDS = {
    SocialChannel.INSTAGRAM: 'name,username,profile_pic',
    SocialChannel.MESSENGER: 'first_name,last_name,profile_pic',
}

_PROFILE_STALE = timedelta(days=7)
_PROFILE_FAILED_RETRY = timedelta(hours=24)
_AVATAR_MAX_BYTES = int(
    getattr(settings, 'SOCIAL_AVATAR_MAX_BYTES', 5 * 1024 * 1024) or (5 * 1024 * 1024)
)
_AVATAR_TIMEOUT_SECONDS = int(
    getattr(settings, 'SOCIAL_AVATAR_DOWNLOAD_TIMEOUT_SECONDS', 10) or 10
)
_GRAPH_TIMEOUT_SECONDS = 10
_LOCK_TTL_SECONDS = 60

_in_process_locks: dict[str, threading.Lock] = {}
_in_process_locks_guard = threading.Lock()


def absolute_profile_pic_url(url: str) -> str:
    """FileSystemStorage.url is /media/... — browsers need the API host."""
    raw = (url or '').strip()
    if not raw or raw.startswith('http://') or raw.startswith('https://'):
        return raw
    base = (getattr(settings, 'API_BASE_URL', '') or '').rstrip('/')
    if not base:
        return raw
    path = raw if raw.startswith('/') else f'/{raw}'
    return f'{base}{path}'


def contact_avatar_url(contact: SocialContact) -> str:
    """Prefer stored path → public URL; fall back to legacy profile_pic_url."""
    if contact.avatar_path:
        try:
            stored = default_storage.url(contact.avatar_path)
        except Exception:
            stored = contact.avatar_path
        return absolute_profile_pic_url(stored)
    return absolute_profile_pic_url(contact.profile_pic_url)


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


def compose_messenger_display_name(first_name: str = '', last_name: str = '', name: str = '') -> str:
    first = str(first_name or '').strip()
    last = str(last_name or '').strip()
    full = ' '.join(part for part in (first, last) if part).strip()
    if full:
        return full
    return str(name or '').strip()


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

    return {
        'name': compose_messenger_display_name(
            payload.get('first_name') or '',
            payload.get('last_name') or '',
            payload.get('name') or '',
        ),
        'username': '',
        'profile_pic_url': pic,
    }


def needs_profile_refresh(contact: SocialContact, *, force: bool = False) -> bool:
    """
    True when we should call Graph (or re-download an avatar).

    - New / never fetched
    - Successful fetch older than 7 days
    - Failed fetch older than 24 hours
    - Avatar missing after a prior ok fetch (CDN expired / download failed)
    """
    if force:
        return True
    if not contact.profile_fetched_at:
        return True

    age = timezone.now() - contact.profile_fetched_at
    status = (contact.profile_fetch_status or '').strip()

    if status == 'failed':
        return age >= _PROFILE_FAILED_RETRY
    if status == 'unavailable':
        return age >= _PROFILE_STALE

    # ok / empty legacy rows: refresh periodically, or sooner if avatar empty
    if not (contact.avatar_path or contact.profile_pic_url):
        return age >= _PROFILE_FAILED_RETRY
    return age >= _PROFILE_STALE


def _lock_key(contact: SocialContact) -> str:
    return f"social_profile_enrich:{contact.company_id}:{contact.channel}:{contact.external_id}"


def _acquire_enrich_lock(contact: SocialContact) -> bool:
    """Cache lock + in-process lock so a message burst triggers one Graph call."""
    key = _lock_key(contact)
    if not cache.add(key, '1', timeout=_LOCK_TTL_SECONDS):
        return False
    with _in_process_locks_guard:
        lock = _in_process_locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        cache.delete(key)
        return False
    return True


def _release_enrich_lock(contact: SocialContact) -> None:
    key = _lock_key(contact)
    with _in_process_locks_guard:
        lock = _in_process_locks.get(key)
    if lock is not None:
        try:
            lock.release()
        except RuntimeError:
            pass
    cache.delete(key)


def _store_profile_pic(contact: SocialContact, url: str) -> tuple[str, str]:
    """
    Copy a Meta CDN avatar into our storage. Meta URLs expire.

    Returns (avatar_path, public_url). Either may be empty on failure.
    """
    if not url:
        return '', ''
    if url.startswith('/') and not url.startswith('//'):
        # Already a local media path
        return url.lstrip('/'), absolute_profile_pic_url(url)

    try:
        response = requests.get(url, timeout=_AVATAR_TIMEOUT_SECONDS, stream=True)
    except requests.RequestException:
        logger.info(
            "Meta Inbox: avatar download failed contact=%s channel=%s external_id=%s",
            contact.id,
            contact.channel,
            contact.external_id,
        )
        return '', ''

    if not response.ok:
        return '', ''

    content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if content_type and not content_type.startswith('image/'):
        logger.info(
            "Meta Inbox: avatar rejected (content-type=%s) contact=%s channel=%s external_id=%s",
            content_type,
            contact.id,
            contact.channel,
            contact.external_id,
        )
        return '', ''

    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _AVATAR_MAX_BYTES:
                logger.info(
                    "Meta Inbox: avatar rejected (>5MB) contact=%s channel=%s external_id=%s",
                    contact.id,
                    contact.channel,
                    contact.external_id,
                )
                return '', ''
            chunks.append(chunk)
    except requests.RequestException:
        return '', ''

    data = b''.join(chunks)
    if not data:
        return '', ''

    key = f"social_profiles/{contact.company_id}/{contact.channel}_{contact.external_id}.jpg"
    if default_storage.exists(key):
        default_storage.delete(key)
    saved = default_storage.save(key, ContentFile(data))
    try:
        stored_url = default_storage.url(saved)
    except Exception:
        stored_url = saved
    return saved, absolute_profile_pic_url(stored_url)


def fetch_contact_profile(contact: SocialContact, *, page_token: str) -> dict:
    """
    Call Graph for one sender.

    Returns {'parsed': {...}, 'error': None|{code,message}} — never raises.
    """
    fields = _PROFILE_FIELDS.get(contact.channel)
    if not fields or not contact.external_id:
        return {'parsed': {}, 'error': None}

    handler = MetaInboxOAuth()
    params = {'fields': fields, 'access_token': page_token}
    proof = handler._appsecret_proof(page_token)
    if proof:
        params['appsecret_proof'] = proof

    url = f"{handler.graph_api_url}/{contact.external_id}"
    try:
        response = requests.get(url, params=params, timeout=_GRAPH_TIMEOUT_SECONDS)
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Meta Inbox: profile lookup failed for contact=%s channel=%s external_id=%s: %s",
            contact.id,
            contact.channel,
            contact.external_id,
            exc,
        )
        return {
            'parsed': {},
            'error': {'code': None, 'message': str(exc)[:300]},
        }

    if isinstance(payload, dict) and payload.get('error'):
        err = payload['error'] if isinstance(payload['error'], dict) else {}
        code = err.get('code')
        message = err.get('message')
        logger.info(
            "Meta Inbox: profile lookup error contact=%s channel=%s external_id=%s code=%s message=%s",
            contact.id,
            contact.channel,
            contact.external_id,
            code,
            message,
        )
        return {
            'parsed': {},
            'error': {'code': code, 'message': message},
        }

    if not response.ok:
        err = payload.get('error') if isinstance(payload, dict) else {}
        code = (err or {}).get('code')
        message = (err or {}).get('message') or f'HTTP {response.status_code}'
        logger.info(
            "Meta Inbox: profile lookup rejected contact=%s channel=%s external_id=%s code=%s message=%s",
            contact.id,
            contact.channel,
            contact.external_id,
            code,
            message,
        )
        return {
            'parsed': {},
            'error': {'code': code, 'message': message},
        }

    return {'parsed': _parse_profile(contact.channel, payload), 'error': None}


def _mark_fetch(contact: SocialContact, status: str, *, update_extra: list[str] | None = None) -> None:
    contact.profile_fetched_at = timezone.now()
    contact.profile_fetch_status = status
    fields = ['profile_fetched_at', 'profile_fetch_status', 'updated_at']
    if update_extra:
        fields.extend(update_extra)
    contact.save(update_fields=list(dict.fromkeys(fields)))


def ensure_contact_profile(contact: SocialContact, connection, *, force: bool = False) -> None:
    """
    Best-effort profile enrichment. Never raises — webhook ingestion must stay resilient.
    """
    if contact.channel == SocialChannel.WHATSAPP:
        return
    if connection is None:
        return
    if not needs_profile_refresh(contact, force=force):
        return
    if not _acquire_enrich_lock(contact):
        return

    try:
        page_token = connection.get_page_access_token()
        if not page_token:
            _mark_fetch(contact, 'unavailable')
            return

        result = fetch_contact_profile(contact, page_token=page_token)
        if result.get('error'):
            _mark_fetch(contact, 'failed')
            return

        parsed = result.get('parsed') or {}
        pic_remote = parsed.get('profile_pic_url', '')
        avatar_path = ''
        public_url = ''
        if pic_remote:
            avatar_path, public_url = _store_profile_pic(contact, pic_remote)

        got_anything = any(
            parsed.get(f) for f in ('name', 'username')
        ) or bool(avatar_path or public_url)

        if not got_anything:
            # Empty success body — treat as unavailable so we backoff, not hammer Graph.
            _mark_fetch(contact, 'unavailable')
            return

        update_fields = ['profile_fetched_at', 'profile_fetch_status', 'updated_at']
        contact.profile_fetched_at = timezone.now()
        contact.profile_fetch_status = 'ok'

        if not contact.name_manually_set:
            new_name = parsed.get('name', '')
            if new_name and contact.name != new_name:
                contact.name = new_name
                update_fields.append('name')

        new_username = parsed.get('username', '')
        if new_username and contact.username != new_username:
            contact.username = new_username
            update_fields.append('username')

        if avatar_path and contact.avatar_path != avatar_path:
            contact.avatar_path = avatar_path
            update_fields.append('avatar_path')
        if public_url and contact.profile_pic_url != public_url:
            contact.profile_pic_url = public_url
            update_fields.append('profile_pic_url')
        elif avatar_path and not public_url:
            # Keep profile_pic_url in sync via storage url when absolute failed
            try:
                contact.profile_pic_url = absolute_profile_pic_url(default_storage.url(avatar_path))
                update_fields.append('profile_pic_url')
            except Exception:
                pass

        contact.save(update_fields=list(dict.fromkeys(update_fields)))
    except Exception:
        logger.exception(
            "Meta Inbox: ensure_contact_profile crashed contact=%s channel=%s external_id=%s",
            contact.id,
            contact.channel,
            contact.external_id,
        )
        try:
            _mark_fetch(contact, 'failed')
        except Exception:
            pass
    finally:
        _release_enrich_lock(contact)


def enrich_contact(
    channel: str,
    external_id: str,
    *,
    company=None,
    connection=None,
    force: bool = False,
) -> SocialContact | None:
    """
    Upsert-aware enrichment entry point (channel + external_id).

    Resolves the SocialContact (optionally scoped by company/connection) and
    runs ensure_contact_profile. Returns the contact, or None if not found.
    """
    qs = SocialContact.objects.filter(channel=channel, external_id=str(external_id).strip())
    if company is not None:
        qs = qs.filter(company=company)
    if connection is not None:
        qs = qs.filter(connection=connection)
    contact = qs.select_related('connection', 'company').first()
    if contact is None:
        return None
    conn = connection or contact.connection
    ensure_contact_profile(contact, conn, force=force)
    contact.refresh_from_db()
    return contact


def run_enrich_contact_task(contact_id: int, connection_id: int | None = None, force: bool = False) -> None:
    """django_q worker entry — lookup by id so the task payload stays small."""
    try:
        contact = SocialContact.objects.select_related('connection').get(pk=contact_id)
    except SocialContact.DoesNotExist:
        return
    connection = None
    if connection_id:
        connection = MetaInboxConnection.objects.filter(pk=connection_id).first()
    connection = connection or contact.connection
    ensure_contact_profile(contact, connection, force=force)


def schedule_ensure_contact_profile(contact: SocialContact, connection, *, force: bool = False) -> None:
    """
    Never block the webhook response. Prefer django_q; fall back to a daemon thread;
    in tests (META_INBOX_PROFILE_ENRICH_SYNC) run inline.
    """
    if contact.channel == SocialChannel.WHATSAPP:
        return
    if not needs_profile_refresh(contact, force=force):
        return

    if getattr(settings, 'META_INBOX_PROFILE_ENRICH_SYNC', False):
        try:
            ensure_contact_profile(contact, connection, force=force)
        except Exception:
            logger.exception(
                "Meta Inbox: sync profile enrichment failed contact=%s", contact.id
            )
        return

    contact_id = contact.id
    connection_id = getattr(connection, 'id', None)

    try:
        from django_q.tasks import async_task

        async_task(
            'integrations.services.meta_inbox_profile.run_enrich_contact_task',
            contact_id,
            connection_id,
            force,
            task_name=f'enrich_social_contact:{contact_id}'[:100],
        )
        return
    except Exception:
        logger.info(
            "Meta Inbox: django_q unavailable; threading enrichment contact=%s",
            contact_id,
        )

    def _run():
        try:
            run_enrich_contact_task(contact_id, connection_id, force)
        except Exception:
            logger.exception(
                "Meta Inbox: threaded profile enrichment failed contact=%s", contact_id
            )

    threading.Thread(target=_run, daemon=True).start()


def refresh_profiles_for_conversations(conversations, *, limit: int = 10) -> None:
    """Best-effort: fill missing names/avatars for the visible inbox page (async)."""
    refreshed = 0
    for conversation in conversations:
        if refreshed >= limit:
            break
        contact = getattr(conversation, 'contact', None)
        if contact is None or contact.channel == SocialChannel.WHATSAPP:
            continue
        if not needs_profile_refresh(contact, force=False):
            # Still force when name+avatar both empty even if status says ok with empty fields
            missing_name = not (contact.name or '').strip() and not (contact.username or '').strip()
            missing_pic = not (contact.avatar_path or contact.profile_pic_url or '').strip()
            if not (missing_name or missing_pic):
                continue
        connection = getattr(conversation, 'connection', None)
        if connection is None:
            continue
        try:
            schedule_ensure_contact_profile(contact, connection, force=True)
            refreshed += 1
        except Exception:
            logger.exception(
                'Meta Inbox: list profile refresh failed contact=%s',
                contact.id,
            )
