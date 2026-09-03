from __future__ import annotations

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import success_response

from .cache import BADGES_CACHE_TTL, badges_cache_key
from .version import digest_token, normalize_etag, slice_versions
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

# The digest is polled every few seconds by every open tab, so it is the single
# hottest endpoint on the platform — and the overwhelmingly common answer is
# "nothing changed". Two mechanisms keep that answer cheap.
#
# 1. The version token (sync/version.py). Every write path that can move a count
#    bumps a counter in the cache; the token folds those counters together. A
#    client that sends a matching token back in If-None-Match is answered with a
#    bare 304 after one cache read and *zero* database queries. This is where
#    almost all of the savings come from.
#
#    An earlier version hashed the built payload instead, which meant the whole
#    digest had to be computed before it could be compared — the 304 saved bytes
#    but not a single query, and no client sent the header anyway.
#
# 2. Tiering, for the rebuilds that do happen. The counts are not equally urgent:
#
#      live   — drives toasts/modals (ringing call, screen pop, walk-in arrival).
#      badges — sidebar unread counts. This tier holds the expensive queries
#               (tenant_chat_unread scans message history), so it is cached under
#               a token-derived key, which any bump rotates (see sync/cache.py).
#
# Freshness is unchanged: a real event bumps the counter and surfaces on the very
# next poll. The token's time bucket bounds the damage if a bump is ever missed.


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


def build_digest(user, token: str | None = None) -> dict:
    """Full digest, badge tier served from cache when warm."""
    if token is None:
        token = digest_token(user)

    key = badges_cache_key(user.id, token)
    badges = cache.get(key)
    if not isinstance(badges, dict):
        badges = build_badges(user)
        cache.set(key, badges, BADGES_CACHE_TTL)

    return {
        **badges,
        **build_live(user),
        "version": token,
        # Per-slice counters, so a client can refetch only what actually moved
        # instead of running a timer per query. Built here rather than inside the
        # badge cache: the cached tier is keyed by the token, so a cached entry is
        # only ever reachable while these values are unchanged anyway — but
        # recomputing them keeps the two independent, and it is one MGET.
        "versions": slice_versions(user),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def sync_digest(request):
    user = request.user
    inm = normalize_etag(request.META.get("HTTP_IF_NONE_MATCH", ""))
    token = digest_token(user)

    # Deliberately before build_digest: the point of the token is that this branch
    # costs one cache read and no database work.
    if inm and inm == token:
        resp = HttpResponse(status=304)
        resp["ETag"] = f'"{token}"'
        resp["Cache-Control"] = "no-store"
        return resp

    return success_response(
        data=build_digest(user, token),
        headers={"ETag": f'"{token}"', "Cache-Control": "no-store"},
    )
