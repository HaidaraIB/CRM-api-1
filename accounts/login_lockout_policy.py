"""Per-account temporary login lockout after failed password attempts."""

from datetime import timedelta

from django.utils import timezone

from settings.models import SystemSettings


def get_login_lockout_settings():
    """Return enabled / max_attempts / duration_minutes from SystemSettings."""
    s = SystemSettings.get_settings()
    return {
        "enabled": bool(s.login_lockout_enabled),
        "max_attempts": max(1, int(s.login_max_failed_attempts or 5)),
        "duration_minutes": max(1, int(s.login_lockout_duration_minutes or 15)),
    }


def lockout_retry_after_seconds(user) -> int:
    """Seconds remaining until unlock; 0 if not locked."""
    until = getattr(user, "lockout_until", None)
    if not until:
        return 0
    remaining = int((until - timezone.now()).total_seconds())
    return max(0, remaining)


def is_account_locked(user) -> bool:
    """True when lockout_until is in the future."""
    if not user:
        return False
    until = getattr(user, "lockout_until", None)
    if not until:
        return False
    return until > timezone.now()


def record_failed_login(user) -> bool:
    """
    Increment failed attempts for ``user``.

    When attempts reach the configured max, set ``lockout_until``, reset the
    attempt counter (so post-unlock starts fresh), and return True.
    Returns False when the account was not locked by this call.
    """
    if not user:
        return False

    settings = get_login_lockout_settings()
    if not settings["enabled"]:
        return False

    attempts = int(getattr(user, "failed_login_attempts", 0) or 0) + 1
    max_attempts = settings["max_attempts"]
    update_fields = ["failed_login_attempts"]

    if attempts >= max_attempts:
        user.failed_login_attempts = 0
        user.lockout_until = timezone.now() + timedelta(minutes=settings["duration_minutes"])
        update_fields.append("lockout_until")
        user.save(update_fields=update_fields)
        return True

    user.failed_login_attempts = attempts
    user.save(update_fields=update_fields)
    return False


def clear_login_failures(user) -> None:
    """Reset attempt counter and clear lockout on successful authentication."""
    if not user:
        return
    if not getattr(user, "failed_login_attempts", 0) and not getattr(user, "lockout_until", None):
        return
    user.failed_login_attempts = 0
    user.lockout_until = None
    user.save(update_fields=["failed_login_attempts", "lockout_until"])


def raise_account_locked(user):
    """Raise AccountLocked with retry_after_seconds from the user's lockout_until."""
    from .exceptions import AccountLocked

    until = getattr(user, "lockout_until", None)
    retry_after = lockout_retry_after_seconds(user)
    raise AccountLocked(
        message="Too many failed login attempts. Please try again later.",
        retry_after_seconds=retry_after,
        lockout_until=until.isoformat() if until else None,
    )
