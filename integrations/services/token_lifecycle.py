"""
Meta/WhatsApp user-token lifecycle helpers.

Meta does not issue classic OAuth refresh tokens. We:
1. Exchange short-lived tokens for ~60-day long-lived tokens (fb_exchange_token)
2. Re-exchange before expiry while the current token is still valid
3. Notify company owner by email + in-app when the token is dead (manual reconnect)
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from ..models import IntegrationAccount, IntegrationLog, IntegrationPlatform
from ..oauth_utils import get_oauth_handler

logger = logging.getLogger(__name__)

# Platforms whose tokens are refreshed by re-exchanging the current access token
# (fb_exchange_token) rather than a refresh_token field. meta_inbox belongs here:
# without it the 60-day user token expires and never renews, and the inbox goes
# silently dead — sends fail with code 190 and webhooks keep arriving unanswered.
META_LIKE_PLATFORMS = frozenset({"meta", "whatsapp", "meta_inbox"})
TOKEN_INVALID_NOTIFY_COOLDOWN = timedelta(hours=24)
REFRESH_BEFORE_EXPIRY = timedelta(days=7)


def upgrade_token_data_to_long_lived(platform: str, token_data: dict) -> dict:
    """Best-effort fb_exchange_token after code exchange. Returns original data on failure."""
    if platform not in META_LIKE_PLATFORMS:
        return token_data
    access = (token_data or {}).get("access_token")
    if not access:
        return token_data
    try:
        oauth = get_oauth_handler(platform)
        long_lived = oauth.refresh_token(access)
        new_access = long_lived.get("access_token")
        if not new_access:
            return token_data
        upgraded = dict(token_data)
        upgraded["access_token"] = new_access
        if long_lived.get("expires_in"):
            upgraded["expires_in"] = long_lived["expires_in"]
        return upgraded
    except Exception as exc:
        logger.warning(
            "Long-lived token exchange failed for platform=%s: %s",
            platform,
            str(exc)[:300],
        )
        return token_data


def apply_refreshed_token(account: IntegrationAccount, token_data: dict) -> None:
    account.set_access_token(token_data["access_token"])
    if token_data.get("refresh_token"):
        account.set_refresh_token(token_data["refresh_token"])
    expires_in = token_data.get("expires_in") or 0
    if expires_in:
        account.token_expires_at = timezone.now() + timedelta(seconds=int(expires_in))
    account.status = "connected"
    account.error_message = None
    metadata = dict(account.metadata or {})
    metadata.pop("token_invalid_notified_at", None)
    account.metadata = metadata
    account.save()
    sync_derived_credentials(account)


def sync_derived_credentials(account: IntegrationAccount) -> None:
    """
    Re-derive credentials that were minted from the user token.

    Hooked here rather than in the cron so every path that replaces a user token
    is covered — the scheduled refresh, ``refresh_access_token_if_needed``, and the
    manual sync in the account viewset alike.

    Meta Inbox is the case that needs it: each connected Page has its own token on
    MetaInboxConnection, derived from the user token. Re-exchanging the user token
    and stopping there leaves those page tokens tied to a user token that no longer
    exists, and the inbox goes quiet with no error anywhere.

    Never raises: the user-token refresh it follows already succeeded and must be
    kept, and the next run retries this.
    """
    if account.platform != IntegrationPlatform.META_INBOX:
        return
    try:
        from .meta_inbox_connections import refresh_meta_inbox_page_tokens

        refresh_meta_inbox_page_tokens(account)
    except Exception:
        logger.exception(
            "Meta Inbox: page token re-derivation failed for account %s", account.id
        )


def refresh_account_token(account: IntegrationAccount) -> dict:
    """
    Refresh/re-exchange token for an IntegrationAccount.
    Meta/WhatsApp: pass current access_token to fb_exchange_token.
    Other platforms: classic refresh_token field.
    """
    oauth = get_oauth_handler(account.platform)
    if account.platform in META_LIKE_PLATFORMS:
        access = account.get_access_token()
        if not access:
            raise ValueError("No access token available")
        token_data = oauth.refresh_token(access)
    else:
        refresh = account.get_refresh_token()
        if not refresh:
            raise ValueError("No refresh token available")
        token_data = oauth.refresh_token(refresh)
    if not token_data.get("access_token"):
        raise ValueError("Refresh response missing access_token")
    apply_refreshed_token(account, token_data)
    return token_data


def _should_notify_token_invalid(account: IntegrationAccount) -> bool:
    metadata = account.metadata or {}
    raw = metadata.get("token_invalid_notified_at")
    if not raw:
        return True
    try:
        last = timezone.datetime.fromisoformat(str(raw))
        if timezone.is_naive(last):
            last = timezone.make_aware(last, timezone.get_current_timezone())
        return timezone.now() - last >= TOKEN_INVALID_NOTIFY_COOLDOWN
    except Exception:
        return True


def _mark_notified(account: IntegrationAccount) -> None:
    metadata = dict(account.metadata or {})
    metadata["token_invalid_notified_at"] = timezone.now().isoformat()
    account.metadata = metadata
    account.save(update_fields=["metadata"])


def notify_owner_token_invalid(account: IntegrationAccount, reason: str = "") -> bool:
    """Email + in-app notify company owner (rate-limited)."""
    if not _should_notify_token_invalid(account):
        return False

    company = getattr(account, "company", None)
    owner = getattr(company, "owner", None) if company else None
    if not owner or not getattr(owner, "email", None):
        logger.warning(
            "Cannot notify token invalid for account %s: no company owner email",
            account.id,
        )
        return False

    from accounts.event_emails import send_integration_token_invalid_email
    from accounts.utils import get_email_language_for_user
    from notifications.models import NotificationType
    from notifications.services import NotificationService

    language = get_email_language_for_user(owner, request=None, default="ar")
    platform_label = account.get_platform_display() if hasattr(account, "get_platform_display") else account.platform
    account_name = account.name or platform_label

    emailed = False
    try:
        emailed = bool(
            send_integration_token_invalid_email(
                owner,
                account=account,
                platform_label=platform_label,
                account_name=account_name,
                reason=reason,
                language=language,
            )
        )
    except Exception as exc:
        logger.error("Failed to send token-invalid email for account %s: %s", account.id, exc)

    if language == "ar":
        title = f"انتهت صلاحية اتصال {platform_label}"
        body = (
            f"انتهت صلاحية توكن حساب «{account_name}». "
            f"أعد الربط من التكاملات حتى تستمر الليدز/الرسائل بالوصول."
        )
    else:
        title = f"{platform_label} connection expired"
        body = (
            f"The token for «{account_name}» is no longer valid. "
            f"Reconnect from Integrations so leads/messages keep flowing."
        )

    try:
        NotificationService.send_notification(
            user=owner,
            notification_type=NotificationType.INTEGRATION_TOKEN_EXPIRED,
            title=title,
            body=body,
            data={
                "account_id": account.id,
                "platform": account.platform,
                "reason": (reason or "")[:300],
            },
            language=language,
            skip_settings_check=True,
        )
    except Exception as exc:
        logger.warning("In-app token-invalid notify failed for account %s: %s", account.id, exc)

    _mark_notified(account)
    return emailed


def mark_account_token_invalid(
    account: IntegrationAccount,
    *,
    error_message: str,
    notify: bool = True,
) -> None:
    account.status = "expired"
    account.error_message = (error_message or "Token is no longer valid")[:500]
    account.save()
    IntegrationLog.objects.create(
        account=account,
        action="token_invalid",
        status="error",
        message="Access token is no longer valid",
        error_details=(error_message or "")[:1000],
    )
    if notify:
        notify_owner_token_invalid(account, reason=error_message)


# --- scheduled entry points ---------------------------------------------------
#
# Called by `manage.py refresh_integration_tokens` (crontab #19), twice a day.
#
# Both are deliberately scoped to META_LIKE_PLATFORMS. The other platforms either
# have no OAuth handler at all (tiktok, api, mujeb all raise from
# get_oauth_handler) or store no refresh token, so widening the query would turn
# every one of them into a failed refresh — and a false "your integration expired"
# email to the owner of a tenant whose integration is fine.


def refreshable_accounts():
    """Connected, active accounts whose tokens this module knows how to refresh."""
    return IntegrationAccount.objects.filter(
        platform__in=META_LIKE_PLATFORMS,
        status="connected",
        is_active=True,
    )


def accounts_due_for_refresh(now=None):
    """Accounts whose token expires within REFRESH_BEFORE_EXPIRY."""
    threshold = (now or timezone.now()) + REFRESH_BEFORE_EXPIRY
    return refreshable_accounts().filter(
        token_expires_at__isnull=False,
        token_expires_at__lte=threshold,
    )


def check_account_token(account: IntegrationAccount) -> tuple[bool | None, str]:
    """
    Ask Meta whether this account's user token is still valid.

    Returns ``(True, "")``, ``(False, reason)``, or ``(None, reason)`` when the
    answer is genuinely unknown. That third state is the point of this function:
    a Graph outage or an unconfigured app credential must never read as "the
    tenant's token is dead", or one bad afternoon at Meta emails every owner on
    the platform telling them to reconnect a working integration.
    """
    access = account.get_access_token()
    if not access:
        return False, "No access token stored"

    try:
        handler = get_oauth_handler(account.platform)
    except ValueError as exc:
        return None, str(exc)

    # debug_token needs an app access token, which needs both app credentials.
    # Without them the handler reports the token invalid — its own failure, not
    # the tenant's.
    if not getattr(handler, "client_id", "") or not getattr(handler, "client_secret", ""):
        return None, f"{account.platform} app credentials are not configured"

    try:
        data = handler.debug_token(access) or {}
    except Exception as exc:
        return None, str(exc)[:300]

    # Our wrapper reports its own app-token failure as a plain string in `error`;
    # Graph's invalid-token answer nests a dict there instead. Only the latter is
    # a verdict about the tenant's token.
    if isinstance(data.get("error"), str):
        return None, data["error"]

    if data.get("is_valid") is False:
        graph_error = data.get("error") if isinstance(data.get("error"), dict) else {}
        return False, graph_error.get("message") or "Meta reported the token as invalid"

    return True, ""


def refresh_expired_tokens() -> dict:
    """Re-exchange every long-lived token nearing expiry."""
    result = {"refreshed": 0, "expired": 0, "failed": 0}

    # Ids first: marking an account expired inside the loop moves it out of the
    # queryset's own filter.
    account_ids = list(accounts_due_for_refresh().values_list("id", flat=True))

    for account_id in account_ids:
        account = IntegrationAccount.objects.filter(pk=account_id).first()
        if account is None:
            continue
        try:
            refresh_account_token(account)
            result["refreshed"] += 1
            continue
        except Exception as exc:
            reason = str(exc)[:300]

        # A refresh can fail because Graph was briefly unavailable, so confirm the
        # token is actually dead before telling the owner to reconnect. Running
        # twice a day inside a 7-day window leaves ~13 more attempts.
        is_valid, detail = check_account_token(account)
        if is_valid is False:
            mark_account_token_invalid(account, error_message=detail or reason)
            result["expired"] += 1
        else:
            result["failed"] += 1
            logger.warning(
                "Token refresh failed for account %s (platform=%s), token still "
                "appears valid — will retry: %s",
                account.id,
                account.platform,
                reason,
            )

    return result


def validate_meta_tokens() -> dict:
    """
    Flag accounts whose token Meta no longer accepts.

    Complements the refresh sweep above, which only looks at accounts with a known
    expiry. A token revoked from the user's Facebook settings, or invalidated by a
    password change, dies long before token_expires_at and would otherwise be
    discovered by an inbound webhook nobody is watching.
    """
    result = {"checked": 0, "valid": 0, "expired": 0, "unknown": 0}

    account_ids = list(refreshable_accounts().values_list("id", flat=True))

    for account_id in account_ids:
        account = IntegrationAccount.objects.filter(pk=account_id).first()
        if account is None:
            continue
        result["checked"] += 1
        is_valid, detail = check_account_token(account)
        if is_valid is False:
            mark_account_token_invalid(account, error_message=detail)
            result["expired"] += 1
        elif is_valid is None:
            result["unknown"] += 1
            logger.info(
                "Could not determine token validity for account %s (platform=%s): %s",
                account.id,
                account.platform,
                detail,
            )
        else:
            result["valid"] += 1

    return result
