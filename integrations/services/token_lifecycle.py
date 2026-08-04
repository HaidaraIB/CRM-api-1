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

from ..models import IntegrationAccount, IntegrationLog
from ..oauth_utils import get_oauth_handler

logger = logging.getLogger(__name__)

META_LIKE_PLATFORMS = frozenset({"meta", "whatsapp"})
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
