"""Language used when seeding per-company starter rows."""

from __future__ import annotations


def seed_language_for_company(company) -> str:
    """Owner preference at company creation. Arabic when none is stored."""
    owner = getattr(company, "owner", None)
    raw = str(getattr(owner, "language", "") or "").strip().lower()
    if raw.startswith("en"):
        return "en"
    return "ar"


def seed_label(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


def ui_language_from_request(request, default: str = "ar") -> str:
    """First UI language from X-Language or Accept-Language. Ignores the DB default."""
    if request is None:
        return default
    for key in ("HTTP_X_LANGUAGE", "HTTP_ACCEPT_LANGUAGE"):
        raw = (request.META.get(key) or "").strip().lower()
        primary = raw.split(",")[0].split(";")[0].strip()
        if primary.startswith("en"):
            return "en"
        if primary.startswith("ar"):
            return "ar"
    return default if default in ("ar", "en") else "ar"
