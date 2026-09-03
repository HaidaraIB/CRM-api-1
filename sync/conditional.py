"""
Conditional GET support for the endpoints clients poll.

The digest proved the shape: fold the counters that can change a response into an
opaque token, hand it back as an ``ETag``, and answer a matching ``If-None-Match``
with a bare 304 before doing any work. This module is that mechanism made reusable,
so a list endpoint gets it in about four lines:

    token = conditional_token(request.user, slices=("calls",), variant=...)
    cached = not_modified(request, token)
    if cached is not None:
        return cached
    ...
    return tag(success_response(data=...), token)

Order matters: build the token and check it *before* running the queryset. A 304
that is produced after the response has already been built saves bytes and nothing
else, which is what the digest used to do before it was fixed.

Three things must be folded into every token, and leaving any of them out is a
correctness bug rather than a missed optimisation:

* **The counters** the response depends on, so it changes when the data does.
* **The user id**, because these endpoints are ACL-filtered — the same URL returns
  different rows to different people. Without it, a token surviving a logout could
  be replayed against a colleague's view of the same list.
* **A hash of the request parameters**, because paging and filters change the
  response without changing any counter. Without it, changing the page would be
  answered 304 with the previous page's contents.

The time bucket from ``sync.version`` is inherited as the same safety net it is
there: a missed bump costs up to ``SAFETY_BUCKET_SECONDS`` of staleness, not a
permanently stuck client.
"""

from __future__ import annotations

import hashlib
import time

from django.core.cache import cache
from django.http import HttpResponse

from .version import (
    SAFETY_BUCKET_SECONDS,
    company_slice_key,
    normalize_etag,
    user_seq_key,
)


def conditional_token(
    user,
    *,
    slices: tuple[str, ...] = (),
    include_user_seq: bool = False,
    variant: str = "",
) -> str:
    """
    Version token for one user's view of one request.

    ``slices`` names the company counters the response depends on (see
    ``COMPANY_SLICE_PREFIXES``). ``include_user_seq`` adds this user's own counter,
    which is needed by anything whose content reflects the viewer's read state —
    a conversation list showing unread markers changes when *they* read something,
    and no company counter moves for that.

    ``variant`` must capture every request parameter that changes the response.

    One ``get_many`` and no database access, so the 304 path stays free.
    """
    company_id = getattr(user, "company_id", None)

    keys: list[str] = []
    if company_id:
        keys.extend(company_slice_key(name, company_id) for name in slices)
    if include_user_seq:
        keys.append(user_seq_key(user.id))

    values = cache.get_many(keys) if keys else {}

    # Slice names go into the hashed material, not just the key list, so two
    # endpoints watching different counters can never produce the same token from
    # coincidentally equal sequence values.
    material = "|".join((*slices, variant))

    parts = [str(getattr(user, "id", 0)), str(company_id or 0)]
    parts.extend(str(values.get(key) or 0) for key in keys)
    parts.append(hashlib.md5(material.encode("utf-8")).hexdigest()[:8] if material else "0")
    parts.append(str(int(time.time() // SAFETY_BUCKET_SECONDS)))
    return ".".join(parts)


def not_modified(request, token: str) -> HttpResponse | None:
    """
    A 304 when the client's copy is current, otherwise ``None``.

    Returning ``None`` rather than raising keeps the caller's happy path linear,
    and makes the "check before you query" ordering visible at the call site.
    """
    if not token:
        return None
    if normalize_etag(request.META.get("HTTP_IF_NONE_MATCH", "")) != token:
        return None
    response = HttpResponse(status=304)
    response["ETag"] = f'"{token}"'
    response["Cache-Control"] = "no-store"
    return response


def tag(response, token: str):
    """
    Attach the token to a 200 so the client can present it next time.

    ``no-store`` because these are per-user, ACL-filtered payloads: the ETag is for
    our own revalidation, and any shared cache holding the body would be a leak.
    """
    response["ETag"] = f'"{token}"'
    response["Cache-Control"] = "no-store"
    return response
