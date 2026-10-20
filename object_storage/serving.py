"""Hand a stored file to a client after the caller's own access check."""

from __future__ import annotations

import logging

from django.http import FileResponse, HttpResponseRedirect
from django.utils.http import content_disposition_header

from .backends import is_remote

logger = logging.getLogger(__name__)


def file_response(
    storage,
    name: str,
    *,
    filename: str,
    content_type: str | None = None,
    as_attachment: bool = False,
    cache_control: str = "private, max-age=3600",
):
    """
    Object storage: a 302 to a presigned GET that carries the Content-Disposition
    and Content-Type, so the bytes never pass through a gunicorn slot. Local disk:
    stream the file.

    A browser that ``fetch()``es the redirect from another origin reaches the
    bucket with ``Origin: null``; the bucket's CORS rule must allow it
    (docs/STORAGE.md, CORS). ``<img>``/``<a>``/``<audio>`` need no CORS.

    Raises FileNotFoundError when a local file is missing; a missing object is a
    404 from the bucket instead.
    """
    if is_remote(storage):
        params = {"ResponseContentDisposition": content_disposition_header(as_attachment, filename)}
        if content_type:
            params["ResponseContentType"] = content_type
        response = HttpResponseRedirect(storage.url(name, parameters=params))
        # The target URL expires; never let a cache replay this redirect.
        response["Cache-Control"] = "private, no-store"
        logger.info("object_storage.redirect bucket=%s key=%s", getattr(storage, "bucket_name", ""), name)
        return response
    response = FileResponse(
        storage.open(name, "rb"),
        as_attachment=as_attachment,
        filename=filename,
        content_type=content_type or None,
    )
    response["Cache-Control"] = cache_control
    return response


def field_file_response(field_file, **kwargs):
    """:func:`file_response` for a ``FieldFile`` (``msg.attachment``)."""
    return file_response(field_file.storage, field_file.name, **kwargs)
