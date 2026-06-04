"""Platform maintenance mode policy (DB + cache + optional env override)."""

from __future__ import annotations

import os

from django.core.cache import cache

MAINTENANCE_CACHE_KEY = "platform_maintenance_policy"
MAINTENANCE_CACHE_TTL = 30

DEFAULT_MAINTENANCE_MESSAGE = (
    "The system is under maintenance. Please try again later."
)

LOCALIZED_DEFAULT_MESSAGES: dict[str, str] = {
    "en": DEFAULT_MAINTENANCE_MESSAGE,
    "ar": "النظام قيد الصيانة حالياً. يرجى المحاولة لاحقاً.",
}


def normalize_request_language(lang: str | None) -> str:
    """Map request language to supported code (en/ar)."""
    if not lang:
        return "en"
    code = str(lang).strip().lower()[:2]
    return code if code in LOCALIZED_DEFAULT_MESSAGES else "en"


def resolve_maintenance_message(
    raw_message: str | None,
    *,
    lang: str | None = None,
) -> str:
    """
    Return user-facing maintenance text.
    Default English DB/env message is replaced with the requested locale.
    Custom admin messages are returned as stored.
    """
    lang_code = normalize_request_language(lang)
    trimmed = (raw_message or "").strip()
    if not trimmed or trimmed == DEFAULT_MAINTENANCE_MESSAGE:
        return LOCALIZED_DEFAULT_MESSAGES[lang_code]
    return trimmed


def request_language_from_meta(meta: dict) -> str:
    """Read X-Language from Django request.META."""
    raw = meta.get("HTTP_X_LANGUAGE") or meta.get("HTTP_X-LANGUAGE") or ""
    return normalize_request_language(str(raw) if raw else None)


def _env_maintenance_enabled() -> bool:
    raw = (os.getenv("MAINTENANCE_MODE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def invalidate_maintenance_cache() -> None:
    cache.delete(MAINTENANCE_CACHE_KEY)


def get_maintenance_policy() -> dict[str, str | bool]:
    """
    Returns {enabled: bool, message: str}.
    Env MAINTENANCE_MODE=true forces enabled without reading DB.
    """
    if _env_maintenance_enabled():
        return {
            "enabled": True,
            "message": (
                os.getenv("MAINTENANCE_MESSAGE", "").strip()
                or DEFAULT_MAINTENANCE_MESSAGE
            ),
        }

    cached = cache.get(MAINTENANCE_CACHE_KEY)
    if cached is not None:
        return cached

    from .models import SystemSettings

    s = SystemSettings.get_settings()
    message = (s.maintenance_message or "").strip() or DEFAULT_MAINTENANCE_MESSAGE
    policy = {
        "enabled": bool(s.maintenance_mode),
        "message": message,
    }
    cache.set(MAINTENANCE_CACHE_KEY, policy, MAINTENANCE_CACHE_TTL)
    return policy


def is_maintenance_enabled() -> bool:
    return bool(get_maintenance_policy()["enabled"])
