"""Bilingual text helpers for AI lead insights."""


def normalize_insight_language(lang: str | None) -> str:
    if lang and str(lang).lower().startswith("ar"):
        return "ar"
    return "en"


def pick_insight_text(
    *,
    language: str,
    text_en: str | None = None,
    text_ar: str | None = None,
    legacy: str | None = None,
) -> str:
    """Return insight copy for the requested UI language with sensible fallbacks."""
    en = (text_en or legacy or "").strip()
    ar = (text_ar or "").strip()
    if normalize_insight_language(language) == "ar":
        return ar or en
    return en or ar


def extract_bilingual_pair(payload: dict, base_key: str, *, max_len: int) -> tuple[str, str]:
    """Parse *_en / *_ar (or legacy single key) from an OpenAI lead payload."""
    en = (payload.get(f"{base_key}_en") or payload.get(base_key) or "").strip()
    ar = (payload.get(f"{base_key}_ar") or "").strip()
    if en and not ar:
        ar = en
    if ar and not en:
        en = ar
    return en[:max_len], ar[:max_len]
