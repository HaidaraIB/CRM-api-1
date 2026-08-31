"""Ephemeral peer activity for tenant DMs (typing, media, voice) stored in Django cache."""

from __future__ import annotations

import time
from typing import Any

from django.core.cache import cache

from .models import ChatConversation

PRESENCE_CACHE_PREFIX = "tenant_chat:presence:v1"
PRESENCE_TTL_SECONDS = 12

VALID_ACTIONS = frozenset(
    {
        "idle",
        "typing",
        "uploading_media",
        "recording_voice",
        "sending_message",
    }
)


def _cache_key(conversation_id: int, user_id: int) -> str:
    return f"{PRESENCE_CACHE_PREFIX}:{conversation_id}:{user_id}"


def other_participant_id(conversation: ChatConversation, user_id: int) -> int:
    if conversation.kind != ChatConversation.Kind.DIRECT:
        raise ValueError("other_participant_id is only valid for direct conversations")
    if conversation.participant_low_id == user_id:
        return conversation.participant_high_id
    return conversation.participant_low_id


def set_user_presence(conversation_id: int, user_id: int, action: str) -> None:
    if action == "idle":
        cache.delete(_cache_key(conversation_id, user_id))
        return
    payload: dict[str, Any] = {"action": action, "ts": time.time()}
    cache.set(_cache_key(conversation_id, user_id), payload, timeout=PRESENCE_TTL_SECONDS)


def _presence_action(raw: Any) -> str | None:
    if not raw or not isinstance(raw, dict):
        return None
    action = raw.get("action")
    if action not in VALID_ACTIONS or action == "idle":
        return None
    return str(action)


def get_user_presence(conversation_id: int, user_id: int) -> str | None:
    return _presence_action(cache.get(_cache_key(conversation_id, user_id)))


def get_users_presence(conversation_id: int, user_ids) -> dict[int, str]:
    """
    Presence for many users in one cache round trip.

    The group branch of peer-presence is polled every couple of seconds and can
    cover up to 100 members; asking for them one at a time is 100 sequential
    Redis round trips per poll per viewer. Returns only users who are currently
    active, so callers can treat a missing key as idle.
    """
    user_ids = list(user_ids)
    if not user_ids:
        return {}
    keys = {_cache_key(conversation_id, uid): uid for uid in user_ids}
    found = cache.get_many(list(keys))
    out = {}
    for key, raw in found.items():
        action = _presence_action(raw)
        if action:
            out[keys[key]] = action
    return out
