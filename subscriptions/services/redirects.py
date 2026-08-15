"""
Frontend redirects for gateway return handlers.

Every gateway return endpoint sends the browser back to the same frontend page.
Building that URL by hand (as the gateway views used to) interpolated raw
exception text straight into a query string without encoding; this module is the
single place that builds it, with urlencode applied to every value.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect

PAYMENT_RESULT_PATH = "/payment/success"

# Shown instead of str(exc) when an unexpected error reaches the browser.
GENERIC_ERROR_MESSAGE = "Could not complete payment verification"


def payment_redirect(
    *,
    status: str,
    subscription_id: Optional[Any] = None,
    message: Optional[str] = None,
    **extra: Any,
):
    """
    Redirect to the frontend payment-result page.

    status: success | failed | pending | error
    extra: additional query params (e.g. tranRef, session_id); falsy values are dropped.
    """
    params: dict[str, Any] = {"status": status}
    if subscription_id:
        params["subscription_id"] = subscription_id
    if message:
        params["message"] = message
    params.update({key: value for key, value in extra.items() if value})

    base = settings.FRONTEND_URL.rstrip("/")
    return redirect(f"{base}{PAYMENT_RESULT_PATH}?{urlencode(params)}")
