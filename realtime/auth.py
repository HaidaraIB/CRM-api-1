"""
Authentication for the WebSocket handshake.

A browser WebSocket cannot set an Authorization header, so the access token
arrives in the query string. That is the standard workaround and it is acceptable
here for one reason: the connection is wss:// in production, so the URL is inside
the TLS session and not visible on the wire.

It is still a URL, though, which is why the token must be short-lived — this
validates the *access* token, not the refresh token. A leaked access token from a
proxy log expires on its own; a refresh token would not.

Note nginx must not log the query string for /ws/ (see deploy notes), or the token
lands in the access log in plain text.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _parse_token(scope) -> str:
    from urllib.parse import parse_qs

    raw = scope.get("query_string") or b""
    try:
        params = parse_qs(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return ""
    values = params.get("token") or []
    return values[0] if values else ""


def authenticate_scope(scope):
    """
    Resolve the connecting user, or None.

    Returning None rather than raising keeps the consumer's connect() simple: it
    closes with a policy-violation code and the client backs off and retries,
    which is also what happens when an access token expires mid-session.
    """
    token = _parse_token(scope)
    if not token:
        return None

    try:
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.exceptions import TokenError
        from rest_framework_simplejwt.tokens import AccessToken
    except Exception:  # pragma: no cover - import guard
        return None

    try:
        access = AccessToken(token)
    except TokenError:
        return None
    except Exception as exc:  # pragma: no cover - malformed input
        logger.debug("Rejected websocket token (%s)", exc)
        return None

    user_id = access.get("user_id")
    if user_id is None:
        return None

    User = get_user_model()
    # Re-read rather than trusting claims: a user deactivated or moved to another
    # company since the token was issued must not keep receiving their old
    # company's events for the token's remaining lifetime.
    user = User.objects.filter(pk=user_id, is_active=True).first()
    return user
