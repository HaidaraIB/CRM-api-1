"""
Meta Inbox connection management: select a Page, subscribe it, keep it healthy.

A "connection" is one Facebook Page plus (optionally) its linked Instagram
professional account. Both channels send through the Page endpoint, so the page
access token is the only credential that matters at runtime — it is stored
encrypted on MetaInboxConnection and never in IntegrationAccount.metadata, which
is returned to clients.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ..models import IntegrationLog, MetaInboxConnection
from ..oauth_utils import MetaInboxOAuth

logger = logging.getLogger(__name__)


class PageConnectError(Exception):
    """Raised with a client-safe error_key when a Page cannot be connected."""

    def __init__(self, error_key: str, message: str):
        super().__init__(message)
        self.error_key = error_key
        self.message = message


def list_grantable_pages(account) -> list[dict]:
    """
    Pages the connecting user granted, refreshed from Graph.

    Falls back to the snapshot cached at OAuth time so the picker still renders
    when Graph is briefly unavailable.
    """
    handler = MetaInboxOAuth()
    token = account.get_access_token()
    if not token:
        return []
    try:
        pages = handler.get_pages(token) or []
        return [
            {'id': str(p.get('id') or ''), 'name': p.get('name') or ''}
            for p in pages
            if p.get('id')
        ]
    except Exception as exc:
        logger.warning("Meta Inbox: get_pages failed, using cached list: %s", exc)
        cached = (account.metadata or {}).get('available_pages') or []
        return [p for p in cached if isinstance(p, dict) and p.get('id')]


def connect_page(account, page_id: str) -> MetaInboxConnection:
    """
    Resolve a page token, link the Instagram account, subscribe to webhooks, and
    persist the connection.

    Raises PageConnectError with an error_key on any step the tenant must act on.
    """
    handler = MetaInboxOAuth()
    user_token = account.get_access_token()
    if not user_token:
        raise PageConnectError(
            'meta_inbox_not_connected',
            'Reconnect the Meta Inbox integration before selecting a Page.',
        )

    page_id = str(page_id or '').strip()
    if not page_id:
        raise PageConnectError('meta_inbox_page_required', 'A Page must be selected.')

    # A Page may already belong to a DIFFERENT company. page_id is globally unique
    # because the webhook resolves the tenant from it, so this must fail loudly
    # rather than silently retarget another tenant's conversations.
    clash = (
        MetaInboxConnection.objects.filter(page_id=page_id)
        .exclude(company_id=account.company_id)
        .first()
    )
    if clash:
        raise PageConnectError(
            'meta_inbox_page_taken',
            'This Facebook Page is already connected to another account.',
        )

    page_token = None
    try:
        page_token = handler.get_page_access_token(page_id, user_token)
    except Exception as exc:
        logger.warning("Meta Inbox: page token fetch failed for %s: %s", page_id, exc)
    if not page_token:
        raise PageConnectError(
            'meta_inbox_page_token_unavailable',
            'Could not obtain a Page access token. Confirm you are an admin of this Page and reconnect.',
        )

    page_name = ''
    for page in (account.metadata or {}).get('available_pages') or []:
        if isinstance(page, dict) and str(page.get('id') or '') == page_id:
            page_name = page.get('name') or ''
            break

    # No linked Instagram account is a supported setup (Messenger-only tenant),
    # so this stays a soft outcome rather than an error.
    ig = handler.get_page_instagram_account(page_id, page_token)

    if ig and ig.get('id'):
        ig_clash = (
            MetaInboxConnection.objects.filter(ig_user_id=ig['id'])
            .exclude(company_id=account.company_id)
            .first()
        )
        if ig_clash:
            raise PageConnectError(
                'meta_inbox_instagram_taken',
                'The Instagram account linked to this Page is already connected elsewhere.',
            )

    subscribe_result = handler.subscribe_page(page_id, page_token)
    subscribed_ok = bool(subscribe_result.get('success')) or 'error' not in subscribe_result

    connection, _created = MetaInboxConnection.objects.update_or_create(
        page_id=page_id,
        defaults={
            'company': account.company,
            'integration_account': account,
            'page_name': page_name,
            'ig_user_id': (ig or {}).get('id') or None,
            'ig_username': (ig or {}).get('username') or '',
            'subscribed_fields': list(handler.SUBSCRIBED_FIELDS) if subscribed_ok else [],
            'instagram_subscribed': bool(subscribed_ok and ig and ig.get('id')),
            'messenger_subscribed': bool(subscribed_ok),
            'status': 'connected' if subscribed_ok else 'error',
            'error_message': (
                None
                if subscribed_ok
                else (subscribe_result.get('error') or {}).get('message')
                or 'Page webhook subscription failed.'
            ),
            'token_expires_at': account.token_expires_at,
        },
    )
    connection.set_page_access_token(page_token)
    connection.save(update_fields=['page_access_token'])

    IntegrationLog.objects.create(
        account=account,
        action='meta_inbox_page_subscribed',
        status='success' if subscribed_ok else 'error',
        message=(
            f"Page {page_id} subscribed to messaging webhooks"
            if subscribed_ok
            else f"Page {page_id} subscription failed"
        ),
        response_data={
            'page_id': page_id,
            'ig_user_id': (ig or {}).get('id'),
            'result': subscribe_result,
        },
    )
    return connection


def disconnect_page(connection: MetaInboxConnection) -> None:
    """Mark disconnected and drop the stored token. Meta-side unsubscribe is best-effort."""
    token = connection.get_page_access_token()
    if token:
        try:
            MetaInboxOAuth().unsubscribe_page(connection.page_id, token)
        except Exception as exc:
            logger.warning(
                "Meta Inbox: unsubscribe failed for page %s: %s", connection.page_id, exc
            )

    connection.status = 'disconnected'
    connection.instagram_subscribed = False
    connection.messenger_subscribed = False
    connection.subscribed_fields = []
    connection.set_page_access_token(None)
    connection.save(
        update_fields=[
            'status',
            'instagram_subscribed',
            'messenger_subscribed',
            'subscribed_fields',
            'page_access_token',
            'updated_at',
        ]
    )
    if connection.integration_account_id:
        IntegrationLog.objects.create(
            account=connection.integration_account,
            action='meta_inbox_page_disconnected',
            status='success',
            message=f"Page {connection.page_id} disconnected",
            response_data={'page_id': connection.page_id},
        )


def refresh_meta_inbox_page_tokens(account) -> dict:
    """
    Re-derive the stored Page token for every connection under this account.

    Page tokens are minted from the user token, so once that token is re-exchanged
    the stored ones are orphaned. What makes this easy to miss is that they keep
    working for a while: nothing fails at the moment of the refresh, and the inbox
    only goes quiet later, once Meta stops honouring the old token.

    Called from token_lifecycle.sync_derived_credentials on every user-token
    refresh. A per-page failure leaves the existing token in place — it is still
    the best credential we have, and the next refresh retries.
    """
    user_token = account.get_access_token()
    if not user_token:
        return {'updated': 0, 'unchanged': 0, 'failed': 0}

    handler = MetaInboxOAuth()
    updated = unchanged = failed = 0

    connections = MetaInboxConnection.objects.filter(
        integration_account=account
    ).exclude(status='disconnected')

    for connection in connections:
        try:
            page_token = handler.get_page_access_token(connection.page_id, user_token)
        except Exception as exc:
            logger.warning(
                "Meta Inbox: page token re-derivation failed for page %s: %s",
                connection.page_id,
                exc,
            )
            failed += 1
            continue

        if not page_token:
            failed += 1
            continue

        if page_token == connection.get_page_access_token():
            # Meta usually re-mints an identical token. Skip the write so
            # updated_at keeps meaning "something about this Page changed".
            unchanged += 1
            continue

        connection.set_page_access_token(page_token)
        connection.token_expires_at = account.token_expires_at
        connection.save(
            update_fields=['page_access_token', 'token_expires_at', 'updated_at']
        )
        updated += 1

    if updated or failed:
        IntegrationLog.objects.create(
            account=account,
            action='meta_inbox_page_tokens_refreshed',
            status='error' if failed else 'success',
            message=f"Re-derived {updated} page token(s); {failed} failed",
            response_data={
                'updated': updated,
                'unchanged': unchanged,
                'failed': failed,
            },
        )

    return {'updated': updated, 'unchanged': unchanged, 'failed': failed}


def notify_owner_inbox_connection_broken(connection: MetaInboxConnection) -> bool:
    """
    Tell the company owner a connected Page stopped receiving messages.

    Reuses INTEGRATION_TOKEN_EXPIRED rather than adding a notification type: the
    action the owner has to take is identical (reconnect from Integrations), and a
    new type would need an icon and label added in all three frontends before it
    rendered as anything but a generic row.
    """
    company = getattr(connection, 'company', None)
    owner = getattr(company, 'owner', None) if company else None
    if not owner:
        logger.warning(
            "Cannot notify broken inbox connection %s: company has no owner",
            connection.id,
        )
        return False

    from accounts.utils import get_email_language_for_user
    from notifications.models import NotificationType
    from notifications.services import NotificationService

    language = get_email_language_for_user(owner, request=None, default="ar")
    label = connection.ig_username or connection.page_name or connection.page_id

    if language == "ar":
        title = "توقف صندوق الرسائل عن استقبال الرسائل"
        body = (
            f"لم تعد صفحة «{label}» مشتركة في التطبيق، لذلك لا تصل رسائل "
            f"Instagram/Messenger الجديدة. أعد الربط من التكاملات."
        )
    else:
        title = "Inbox stopped receiving messages"
        body = (
            f"The Page «{label}» is no longer subscribed to the app, so new "
            f"Instagram/Messenger messages are not arriving. Reconnect it from "
            f"Integrations."
        )

    try:
        NotificationService.send_notification(
            user=owner,
            notification_type=NotificationType.INTEGRATION_TOKEN_EXPIRED,
            title=title,
            body=body,
            data={
                'connection_id': connection.id,
                'page_id': connection.page_id,
                'platform': 'meta_inbox',
            },
            language=language,
            skip_settings_check=True,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to notify owner about broken inbox connection %s: %s",
            connection.id,
            exc,
        )
        return False


def check_connection_health(connection: MetaInboxConnection) -> dict:
    """
    Verify our app is still subscribed on the Page.

    Meta keeps accepting sends while quietly delivering no webhooks if the
    subscription was removed in Business Settings, so a silent unsubscribe is
    otherwise invisible until someone notices the inbox went quiet.
    """
    token = connection.get_page_access_token()
    if not token:
        connection.status = 'error'
        connection.error_message = (
            'This Page is missing a messaging token. Enable messaging to resume.'
        )
        connection.messenger_subscribed = False
        connection.instagram_subscribed = False
        connection.save(
            update_fields=[
                'status',
                'error_message',
                'messenger_subscribed',
                'instagram_subscribed',
                'updated_at',
            ]
        )
        return {'ok': False, 'error_key': 'meta_inbox_page_token_missing', 'fields': []}

    handler = MetaInboxOAuth()
    installed, fields, error = handler.get_subscribed_fields(connection.page_id, token)
    if error:
        return {'ok': False, 'error_key': 'meta_inbox_health_check_failed', 'fields': [], 'error': error}

    has_messages = 'messages' in (fields or [])
    if not installed or not has_messages:
        connection.status = 'error'
        connection.error_message = (
            'This Page is no longer subscribed to the app. Reconnect to resume receiving messages.'
        )
        connection.messenger_subscribed = False
        connection.instagram_subscribed = False
        connection.save(
            update_fields=[
                'status',
                'error_message',
                'messenger_subscribed',
                'instagram_subscribed',
                'updated_at',
            ]
        )
    else:
        connection.subscribed_fields = list(fields)
        if connection.status == 'error':
            connection.status = 'connected'
            connection.error_message = None
        connection.save(
            update_fields=['subscribed_fields', 'status', 'error_message', 'updated_at']
        )

    return {
        'ok': bool(installed and has_messages),
        'installed': installed,
        'fields': list(fields or []),
        'checked_at': timezone.now().isoformat(),
    }
