"""Ephemeral TURN credentials for WebRTC (coturn REST-style HMAC)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from django.conf import settings as django_settings


def build_turn_ice_entry(turn_url: str, *, user_id: int, ttl_seconds: int = 3600) -> dict:
    """
    Return an ice_servers entry with time-limited username/credential.

    When PBX_TURN_SHARED_SECRET is unset, returns URL only (static-credential
    TURN must be configured separately — document as operational follow-up).
    """
    entry: dict = {"urls": turn_url}
    secret = (getattr(django_settings, "PBX_TURN_SHARED_SECRET", "") or "").strip()
    if not secret:
        return entry

    expiry = int(time.time()) + ttl_seconds
    username = f"{expiry}:{user_id}"
    digest = hmac.new(
        secret.encode("utf-8"),
        username.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    credential = base64.b64encode(digest).decode("utf-8")
    entry["username"] = username
    entry["credential"] = credential
    return entry
