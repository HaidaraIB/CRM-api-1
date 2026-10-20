"""
Presigned GET URLs (AWS SigV4 query auth, path-style), stable within a window.

botocore signs with the current second, so every API response would carry a new
URL for the same object. That defeats the browser's HTTP cache and Flutter's image
cache (both keyed by URL), re-downloads every avatar on each list refetch, and
makes React Query see changed data on every poll.

Here the signing time is rounded down to ``window`` seconds and the lifetime is
extended by the same amount, so:

* every response inside one window carries the *same* URL for an object, and
* a URL handed out at any moment stays valid for at least ``expires`` seconds.

The algorithm is the standard one; ``tests/test_object_storage.py`` checks it
byte for byte against botocore's own presigner at a frozen clock.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

MAX_EXPIRES = 7 * 24 * 3600  # SigV4 query auth limit (R2 and S3)

# botocore parameter names → S3 response-override query names.
RESPONSE_PARAMS = {
    "ResponseCacheControl": "response-cache-control",
    "ResponseContentDisposition": "response-content-disposition",
    "ResponseContentEncoding": "response-content-encoding",
    "ResponseContentLanguage": "response-content-language",
    "ResponseContentType": "response-content-type",
    "ResponseExpires": "response-expires",
}


def _uri_encode(value: str, *, keep_slash: bool) -> str:
    return quote(value, safe="/~" if keep_slash else "~")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presign_get_url(
    *,
    endpoint_url: str,
    bucket: str,
    key: str,
    region: str,
    access_key: str,
    secret_key: str,
    expires: int,
    window: int = 0,
    parameters: dict | None = None,
    now: float | None = None,
) -> str:
    """
    A path-style presigned GET for ``bucket/key``. ``parameters`` takes botocore
    names (``ResponseContentDisposition`` …). ``window`` = 0 signs at the current
    second, exactly like botocore.
    """
    parts = urlsplit(endpoint_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("endpoint_url must be an http(s) URL")
    now = time.time() if now is None else now
    signed_at = int(now) - (int(now) % window) if window > 0 else int(now)
    lifetime = int(expires) + (window if window > 0 else 0)
    if not 1 <= lifetime <= MAX_EXPIRES:
        raise ValueError(f"presigned URL lifetime must be 1..{MAX_EXPIRES} seconds")

    stamp = datetime.fromtimestamp(signed_at, tz=timezone.utc)
    amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    scope = f"{day}/{region}/s3/aws4_request"

    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(lifetime),
        "X-Amz-SignedHeaders": "host",
    }
    for name, value in (parameters or {}).items():
        if name not in RESPONSE_PARAMS:
            raise ValueError(f"unsupported presign parameter: {name}")
        query[RESPONSE_PARAMS[name]] = str(value)

    base_path = parts.path.rstrip("/")
    path = _uri_encode(f"{base_path}/{bucket}/{key}", keep_slash=True)
    canonical_query = "&".join(
        f"{_uri_encode(k, keep_slash=False)}={_uri_encode(v, keep_slash=False)}" for k, v in sorted(query.items())
    )
    canonical_request = "\n".join(["GET", path, canonical_query, f"host:{parts.netloc}\n", "host", "UNSIGNED-PAYLOAD"])
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    signing_key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{parts.scheme}://{parts.netloc}{path}?{canonical_query}&X-Amz-Signature={signature}"
