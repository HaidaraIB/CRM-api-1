"""
Request-parameter helpers shared by the gateway return handlers.

Gateways are inconsistent about where they put the reference on the way back:
query string on a browser redirect, JSON body when the frontend calls the same
endpoint itself, form-encoded for some server posts. These read from whichever
carries it, without touching request.body (which would consume the stream that
DRF has already parsed into request.data).
"""
from __future__ import annotations

from typing import Any, Optional


def param(request, *keys: str) -> Optional[Any]:
    """First truthy value found under any of `keys`, in the query string or body."""
    sources = [request.GET]
    data = getattr(request, "data", None)
    if isinstance(data, dict):
        sources.append(data)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value:
                return value
    return None


def int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
