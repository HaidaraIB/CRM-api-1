"""
Background tasks for integrations
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import IntegrationAccount, IntegrationLog
from .oauth_utils import MetaOAuth
from .services.token_lifecycle import (
    META_LIKE_PLATFORMS,
    REFRESH_BEFORE_EXPIRY,
    mark_account_token_invalid,
    refresh_account_token,
)

logger = logging.getLogger(__name__)


def refresh_expired_tokens():
    """
    Proactively re-exchange Meta/WhatsApp long-lived tokens before they expire,
    and refresh other platforms that have a classic refresh_token.

    Schedule daily (django-q / cron):
        python manage.py refresh_integration_tokens
    """
    now = timezone.now()
    expiry_threshold = now + REFRESH_BEFORE_EXPIRY

    accounts_to_refresh = IntegrationAccount.objects.filter(
        status="connected",
        token_expires_at__lte=expiry_threshold,
        token_expires_at__isnull=False,
        is_active=True,
    ).select_related("company", "company__owner")

    refreshed_count = 0
    failed_count = 0
    skipped_count = 0

    for account in accounts_to_refresh:
        try:
            if account.platform in META_LIKE_PLATFORMS:
                if not account.get_access_token():
                    skipped_count += 1
                    logger.warning("No access token for Meta-like account %s", account.id)
                    continue
            elif not account.get_refresh_token():
                skipped_count += 1
                logger.warning("No refresh token for account %s", account.id)
                continue

            refresh_account_token(account)
            refreshed_count += 1

            IntegrationLog.objects.create(
                account=account,
                action="auto_refresh_token",
                status="success",
                message="Token refreshed automatically",
            )
            logger.info("Successfully refreshed token for account %s", account.id)

        except Exception as e:
            failed_count += 1
            mark_account_token_invalid(
                account,
                error_message=str(e)[:500],
                notify=True,
            )
            IntegrationLog.objects.create(
                account=account,
                action="auto_refresh_token",
                status="error",
                message="Failed to refresh token automatically",
                error_details=str(e)[:1000],
            )
            logger.error("Failed to refresh token for account %s: %s", account.id, e)

    # Also validate connected Meta accounts that may already be dead
    # (password change / security revoke) even if expires_at is still in the future.
    invalidated = validate_meta_tokens()

    logger.info(
        "Token refresh completed: refreshed=%s failed=%s skipped=%s invalidated=%s",
        refreshed_count,
        failed_count,
        skipped_count,
        invalidated.get("invalidated", 0),
    )

    return {
        "refreshed": refreshed_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "total": accounts_to_refresh.count(),
        **invalidated,
    }


def validate_meta_tokens():
    """
    Call Graph debug_token for connected Meta accounts and mark/notify invalid ones.
    """
    meta_oauth = MetaOAuth()
    accounts = IntegrationAccount.objects.filter(
        platform="meta",
        status="connected",
        is_active=True,
    ).filter(
        Q(access_token__isnull=False) & ~Q(access_token="")
    ).select_related("company", "company__owner")

    checked = 0
    invalidated = 0

    for account in accounts:
        token = account.get_access_token()
        if not token:
            continue
        checked += 1
        try:
            debug_data = meta_oauth.debug_token(token)
        except Exception as e:
            mark_account_token_invalid(account, error_message=str(e)[:500], notify=True)
            invalidated += 1
            continue

        if debug_data.get("is_valid") is True:
            continue

        err_msg = (
            (debug_data.get("error") or {}).get("message")
            or "Token is no longer valid"
        )
        mark_account_token_invalid(account, error_message=err_msg, notify=True)
        invalidated += 1

    return {"checked": checked, "invalidated": invalidated}
