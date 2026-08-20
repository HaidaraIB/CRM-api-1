from __future__ import annotations

import hashlib
import json

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import success_response

from .cache import BADGES_CACHE_TTL, badges_cache_key
from .counts import (
    arrivals_pending_for_user,
    arrivals_waiting_for_user,
    news_unread_for_user,
    notifications_unread_for_user,
    pbx_screen_pop_for_user,
    tenant_chat_unread_for_user,
    whatsapp_calls_pending_for_user,
    whatsapp_unread_for_user,
)

# The digest is polled every 5s by every open tab, so it is the single hottest
# endpoint on the platform. Its eight counts are not equally urgent, and caching
# them as one blob forced a choice between stale alerts and rebuilding all of them
# on every poll (the previous 4s TTL against a 5s poll never hit, so it was always
# the latter).
#
# So they are split by how fresh they actually need to be:
#
#   live   — drives toasts/modals (ringing call, screen pop, walk-in arrival).
#            Must reflect the current poll, so it is rebuilt every request.
#   badges — sidebar unread counts. Nobody notices a badge that is half a minute
#            behind, and this tier holds the expensive queries (tenant_chat_unread
#            scans message history), so it is cached per user and invalidated
#            eagerly by whatever clears the count (see sync/cache.py).
#
# Net effect at the same 5s poll: the badge tier is built ~2x/min instead of
# 12x/min per user, with no added latency on anything a user is waiting for.


def _etag_token(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("W/"):
        t = t[2:].strip()
    if t.startswith('"') and t.endswith('"') and len(t) >= 2:
        t = t[1:-1]
    return t


def build_live(user) -> dict:
    """Counts that drive an alert the user is waiting on. Never cached."""
    return {
        "whatsapp_calls_pending": whatsapp_calls_pending_for_user(user),
        "pbx_screen_pop": pbx_screen_pop_for_user(user),
        "arrivals_pending": arrivals_pending_for_user(user),
    }


def build_badges(user) -> dict:
    """Sidebar unread counts. Cached for BADGES_CACHE_TTL."""
    return {
        "whatsapp_unread": whatsapp_unread_for_user(user),
        "tenant_chat_unread": tenant_chat_unread_for_user(user),
        "notifications_unread": notifications_unread_for_user(user),
        "news_unread": news_unread_for_user(user),
        "arrivals_waiting": arrivals_waiting_for_user(user),
    }


def build_digest(user) -> dict:
    """
    Full digest, badge tier served from cache when warm.

    ``version`` still hashes the merged payload, so the ETag keeps changing
    whenever any field changes — a client holding a 304 is never shown a stale
    live count.
    """
    badges = cache.get(badges_cache_key(user.id))
    if not isinstance(badges, dict):
        badges = build_badges(user)
        cache.set(badges_cache_key(user.id), badges, BADGES_CACHE_TTL)

    payload = {**badges, **build_live(user)}
    version = hashlib.md5(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:6]
    payload["version"] = version
    return payload


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def sync_digest(request):
    user = request.user
    inm = _etag_token(request.META.get("HTTP_IF_NONE_MATCH", ""))
    data = build_digest(user)

    if inm and inm == data["version"]:
        resp = HttpResponse(status=304)
        resp["ETag"] = f'"{data["version"]}"'
        resp["Cache-Control"] = "no-store"
        return resp

    return success_response(
        data=data,
        headers={"ETag": f'"{data["version"]}"', "Cache-Control": "no-store"},
    )
