"""
Billing-scoped access tokens.

A company owner whose subscription has lapsed cannot log in — login refuses to
issue JWTs without an active subscription — yet checkout requires the owner
(`require_subscription_owner`). On its own that is a closed loop: no token
because they have not paid, no way to pay because they have no token.

This mints a short-lived access token carrying `scope="billing"`.
`BillingScopeMiddleware` confines that token to the checkout endpoints, so it
buys the owner a way to pay and nothing else.
"""
import base64
import binascii
import logging
from datetime import timedelta

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)

SCOPE_CLAIM = "scope"
BILLING_SCOPE = "billing"

# Long enough to pick a gateway and finish a hosted checkout, short enough that
# a token left in a shared browser is worthless by the time anyone finds it.
BILLING_TOKEN_LIFETIME = timedelta(minutes=30)


def issue_billing_access_token(user, subscription):
    """
    Checkout-only access token for the owner of `subscription`.

    Returns None for anyone else: staff never get one (their route back in is
    the owner paying), and neither does a user we cannot tie to the
    subscription being paid for.
    """
    if user is None or subscription is None:
        return None

    owner = getattr(getattr(subscription, "company", None), "owner", None)
    if owner is None or owner.id != getattr(user, "id", None):
        return None
    if subscription.company_id != getattr(user, "company_id", None):
        return None

    token = AccessToken.for_user(user)
    token.set_exp(lifetime=BILLING_TOKEN_LIFETIME)
    token[SCOPE_CLAIM] = BILLING_SCOPE
    token["subscription_id"] = subscription.id
    logger.info(
        "Issued billing-scoped token for user_id=%s subscription=%s",
        user.id,
        subscription.id,
    )
    return str(token)


def _carries_scope_claim(raw_token):
    """
    Unverified peek at the payload segment.

    Ordinary login tokens carry no `scope` claim, so this keeps the verified
    decode below off the hot path of every authenticated request. A forged
    claim only buys a trip through `AccessToken()`, which rejects it.
    """
    try:
        payload_b64 = raw_token.split(".")[1]
    except (AttributeError, IndexError):
        return False
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload = base64.urlsafe_b64decode(payload_b64 + padding)
    except (ValueError, binascii.Error):
        return False
    return b'"scope"' in payload


def scope_from_raw_token(raw_token):
    """
    The verified `scope` claim of `raw_token`, or None.

    Invalid or expired tokens return None so DRF still produces its normal 401 —
    this only decides whether a request needs *restricting*, never whether it
    is authenticated.
    """
    if not raw_token or not _carries_scope_claim(raw_token):
        return None
    try:
        token = AccessToken(raw_token)
    except TokenError:
        return None
    scope = token.get(SCOPE_CLAIM)
    return scope if isinstance(scope, str) else None


def bearer_token_from_request(request):
    """Raw JWT from the Authorization header, or None."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        return None
    return parts[1]
