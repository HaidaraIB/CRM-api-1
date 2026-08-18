from __future__ import annotations

import hashlib
import json

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import success_response

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

DIGEST_CACHE_TTL = 4
DIGEST_CACHE_PREFIX = "sync_digest_v1"


def _etag_token(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("W/"):
        t = t[2:].strip()
    if t.startswith('"') and t.endswith('"') and len(t) >= 2:
        t = t[1:-1]
    return t


def build_digest(user) -> dict:
    payload = {
        "whatsapp_unread": whatsapp_unread_for_user(user),
        "whatsapp_calls_pending": whatsapp_calls_pending_for_user(user),
        "tenant_chat_unread": tenant_chat_unread_for_user(user),
        "notifications_unread": notifications_unread_for_user(user),
        "news_unread": news_unread_for_user(user),
        "pbx_screen_pop": pbx_screen_pop_for_user(user),
        "arrivals_pending": arrivals_pending_for_user(user),
        "arrivals_waiting": arrivals_waiting_for_user(user),
    }
    version = hashlib.md5(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:6]
    payload["version"] = version
    return payload


def _cache_key(user_id: int) -> str:
    return f"{DIGEST_CACHE_PREFIX}:{user_id}"


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def sync_digest(request):
    user = request.user
    inm = _etag_token(request.META.get("HTTP_IF_NONE_MATCH", ""))
    cached = cache.get(_cache_key(user.id))
    if isinstance(cached, dict) and cached.get("version"):
        if inm and inm == cached["version"]:
            resp = HttpResponse(status=304)
            resp["ETag"] = f'"{cached["version"]}"'
            resp["Cache-Control"] = "no-store"
            return resp
        data = cached
    else:
        data = build_digest(user)
        cache.set(_cache_key(user.id), data, DIGEST_CACHE_TTL)

    if inm and inm == data["version"]:
        resp = HttpResponse(status=304)
        resp["ETag"] = f'"{data["version"]}"'
        resp["Cache-Control"] = "no-store"
        return resp

    return success_response(
        data=data,
        headers={"ETag": f'"{data["version"]}"', "Cache-Control": "no-store"},
    )
