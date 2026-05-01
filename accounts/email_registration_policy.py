from django.core.cache import cache


EMAIL_VERIFICATION_REQUIRED_CACHE_KEY = "registration_email_verification_required_override"


def effective_registration_email_verification_required() -> bool:
    return bool(cache.get(EMAIL_VERIFICATION_REQUIRED_CACHE_KEY, False))
