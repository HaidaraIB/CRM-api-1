from rest_framework.throttling import AnonRateThrottle


class AuthRateThrottle(AnonRateThrottle):
    """
    Stricter rate limit for authentication endpoints (login, register, etc.).
    Uses the 'auth' rate from DEFAULT_THROTTLE_RATES.
    """
    scope = "auth"
