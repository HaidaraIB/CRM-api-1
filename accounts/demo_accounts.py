"""Store-review demo accounts (Google Play, App Store, Meta App Review)."""
from __future__ import annotations

from django.conf import settings

_DEMO_KINDS = ("google", "apple", "meta")


def _username_setting(kind: str) -> str:
    return (getattr(settings, f"DEMO_{kind.upper()}_ACCOUNT_USERNAME", "") or "").strip()


def _email_setting(kind: str) -> str:
    return (getattr(settings, f"DEMO_{kind.upper()}_ACCOUNT_EMAIL", "") or "").strip()


def _two_fa_setting(kind: str) -> str:
    return (getattr(settings, f"DEMO_{kind.upper()}_ACCOUNT_2FA_CODE", "") or "").strip()


def _matches_demo_user(user, kind: str) -> bool:
    username = _username_setting(kind)
    email = _email_setting(kind)
    if not username and not email:
        return False
    if username and user.username.lower() == username.lower():
        return True
    if email and (user.email or "").lower() == email.lower():
        return True
    return False


def get_demo_account_kind(user) -> str | None:
    """Return 'google', 'apple', 'meta', or None."""
    for kind in _DEMO_KINDS:
        if _matches_demo_user(user, kind):
            return kind
    return None


def get_demo_2fa_code_for_user(user) -> str | None:
    kind = get_demo_account_kind(user)
    if not kind:
        return None
    code = _two_fa_setting(kind)
    return code or None


def is_demo_review_account(user) -> bool:
    return get_demo_account_kind(user) is not None
