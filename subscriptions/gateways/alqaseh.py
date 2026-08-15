"""
Al Qaseh adapter (hosted payment page, non-PCI merchant flow).

Docs: https://docs.alqaseh.com/ — endpoints and field names follow their
OpenAPI 3.0.1 document ("Al-Qaseh e-commerce Payment gateway API").

Two things about this gateway shape the code below:

1. **Two status vocabularies.** The redirect and webhook carry a coarse
   Success/Failed/Pending, while the API reports nine precise states. We ignore
   the payload and re-query, so only the nine matter here.

2. **Three different identifiers.** Creating a payment returns both a `token`
   (which keys the hosted page, `/pay/{token}`) and a `payment_id` (which keys
   the status API, `GET /egw/payments/{id}`), while the webhook carries neither
   — it identifies the transaction by `order_id`, which is *our* value.
   So `tran_ref` holds `payment_id`, and `token` and `order_id` live in
   `session_meta` so every inbound path can still resolve the row.
   See `extract_tran_ref`.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import requests
from django.utils import timezone

from subscriptions.gateways.base import (
    CARD_GROUP,
    BaseGatewayAdapter,
    CheckoutContext,
    CheckoutSession,
    GatewayError,
    GatewayResult,
)
from subscriptions.gateways.registry import register
from subscriptions.services.fx import usd_to_iqd

logger = logging.getLogger(__name__)

TEST_API_BASE = "https://api-test.alqaseh.com/v1"
TEST_PAY_BASE = "https://pay-test.alqaseh.com/pay"

# Endpoints and field names below are taken from Al Qaseh's OpenAPI 3.0.1
# document ("Al-Qaseh e-commerce Payment gateway API"), served by the reference
# at https://docs.alqaseh.com/api.
CREATE_PAYMENT_PATH = "/egw/payments/create"
#: GET /egw/payments/{id} — `id` is the string payment_id, not the numeric row id
STATUS_PATH_TEMPLATE = "/egw/payments/{payment_id}"

# Al Qaseh only publishes test servers; the live hosts are still unconfirmed.
# Both are overridable per-tenant via `baseUrl` / `payBaseUrl` in the gateway
# config, so they can be corrected without a deploy.
LIVE_API_BASE = "https://api.alqaseh.com/v1"
LIVE_PAY_BASE = "https://pay.alqaseh.com/pay"

#: Al Qaseh's token lifetime is expressed in whole hours (minimum 1).
TOKEN_EXPIRY_HOURS = 1

# The nine documented API states, mapped onto our shared vocabulary.
# Derived from Al Qaseh's own rules: a payment may be *retried* when Expired,
# Declined, Failed or Unknown (so that attempt is over), and *revoked* while
# Prepared or Retried (so those are still in flight).
_PAID = {"succeeded"}
_FAILED = {"failed", "declined", "revoked", "expired"}
_PENDING = {"prepared", "retried"}
# "unknown" is retryable but genuinely indeterminate, and "duplicated" means the
# order id collided. Neither is safe to act on, so both leave the payment
# PENDING to be re-polled rather than being marked FAILED.
_INDETERMINATE = {"unknown", "duplicated"}

REQUEST_TIMEOUT = 30


class AlqasehAdapter(BaseGatewayAdapter):
    slug = "alqaseh"
    name_aliases = ("alqaseh", "al qaseh", "al-qaseh", "qaseh")
    # The redirect carries payment_id; the webhook carries only order_id.
    tran_ref_params = ("payment_id", "paymentId")
    exclusive_group = CARD_GROUP

    # -- configuration ----------------------------------------------------

    def _config(self) -> dict:
        from subscriptions.gateways.registry import find_gateway_row

        row = find_gateway_row(self.slug)
        if row is None:
            raise GatewayError("Al Qaseh gateway is not configured or enabled")
        return row.config or {}

    def _is_live(self, config: dict) -> bool:
        return (config.get("environment") or "test").lower() == "live"

    def _api_base(self, config: dict) -> str:
        override = (config.get("baseUrl") or "").strip()
        if override:
            return override.rstrip("/")
        return LIVE_API_BASE if self._is_live(config) else TEST_API_BASE

    def _pay_base(self, config: dict) -> str:
        override = (config.get("payBaseUrl") or "").strip()
        if override:
            return override.rstrip("/")
        return LIVE_PAY_BASE if self._is_live(config) else TEST_PAY_BASE

    def _auth(self, config: dict) -> tuple[str, str]:
        client_id = (config.get("clientId") or "").strip()
        client_secret = (config.get("clientSecret") or "").strip()
        if not client_id or not client_secret:
            raise GatewayError("Al Qaseh credentials are not configured")
        return client_id, client_secret

    # -- HTTP -------------------------------------------------------------

    def _request(self, method: str, path: str, config: dict, **kwargs):
        """Basic-auth JSON call. Raises GatewayError with the server's message."""
        url = f"{self._api_base(config)}{path}"
        try:
            response = requests.request(
                method,
                url,
                auth=self._auth(config),
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            detail = {}
            try:
                detail = exc.response.json()
            except (ValueError, AttributeError):
                detail = {"message": getattr(exc.response, "text", "")}
            message = detail.get("message") or detail.get("error") or str(exc)
            raise GatewayError(
                f"Al Qaseh API error: {message}", details=detail
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise GatewayError(f"Error communicating with Al Qaseh: {exc}") from exc
        except ValueError as exc:  # non-JSON success body
            raise GatewayError("Al Qaseh returned a non-JSON response") from exc

    # -- checkout ---------------------------------------------------------

    def _new_order_id(self, subscription_id: int) -> str:
        """Our own reference. Must be unique per attempt - Al Qaseh reports a
        colliding order id back as the `duplicated` status."""
        return f"SUB-{subscription_id}-{uuid.uuid4().hex[:12]}"

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        config = self._config()
        order_id = self._new_order_id(ctx.subscription_id)
        # Plans are priced in USD; Al Qaseh settles in IQD, which has no minor
        # unit in practice, so the amount is whole dinars.
        amount_iqd = int(usd_to_iqd(ctx.amount_usd))

        # Field names and the required set come from
        # pgb_service.CreatePaymentContextParams: amount, currency, description,
        # order_id, redirect_url and transaction_type are mandatory.
        payload = {
            "amount": amount_iqd,
            "currency": config.get("currency", "IQD"),
            "description": f"Subscription {ctx.subscription_id} - {ctx.target_plan.name}"[:250],
            "order_id": order_id,
            "redirect_url": ctx.return_url,
            "transaction_type": "Retail",
            "token_expiry_in_hour": TOKEN_EXPIRY_HOURS,
            # custom_data is echoed back on the status response, which gives us
            # a server-side trail for reconciliation.
            "custom_data": {
                "subscription_id": str(ctx.subscription_id),
                "plan_id": str(ctx.target_plan.id),
                "billing_cycle": ctx.billing_cycle,
            },
        }
        if ctx.customer_email:
            payload["email"] = ctx.customer_email[:80]
        if ctx.callback_url:
            payload["webhook_url"] = ctx.callback_url

        result = self._request("POST", CREATE_PAYMENT_PATH, config, json=payload) or {}

        # dto.CreatePaymentContextResponse: { payment_id, token }
        token = result.get("token")
        payment_id = result.get("payment_id")
        if not token or not payment_id:
            raise GatewayError(
                "Al Qaseh did not return a payment token", details=result
            )

        return CheckoutSession(
            # The hosted page is keyed by `token`, but the status API is keyed
            # by `payment_id` — so payment_id is what tran_ref must hold.
            tran_ref=str(payment_id),
            checkout_url=f"{self._pay_base(config)}/{token}",
            expires_at=timezone.now() + timedelta(hours=TOKEN_EXPIRY_HOURS),
            meta={
                "order_id": order_id,
                "token": token,
                "amount_iqd": amount_iqd,
            },
        )

    def session_payload(self, payment, session: CheckoutSession) -> dict:
        return {
            "payment_id": payment.id,
            "redirect_url": session.checkout_url,
            "tran_ref": session.tran_ref,
            "order_id": (session.meta or {}).get("order_id"),
        }

    # -- verification -----------------------------------------------------

    def verify(self, tran_ref: str) -> GatewayResult:
        config = self._config()
        path = STATUS_PATH_TEMPLATE.format(payment_id=tran_ref)
        # dto.GetPaymentContextResponse
        result = self._request("GET", path, config) or {}

        state = str(result.get("payment_status") or "").strip().lower()
        if state in _PAID:
            return GatewayResult("paid", result)
        if state in _FAILED:
            return GatewayResult("failed", result)
        if state in _PENDING:
            return GatewayResult("pending", result)
        if state in _INDETERMINATE:
            logger.info(
                "Al Qaseh reported indeterminate status %r for %s", state, tran_ref
            )
            return GatewayResult("unknown", result)

        logger.warning("Unrecognised Al Qaseh status %r for %s", state, tran_ref)
        return GatewayResult("unknown", result)

    # -- reference extraction ---------------------------------------------

    def extract_tran_ref(self, request):
        """
        The redirect carries `payment_id`; the webhook carries only `order_id`,
        so fall back to resolving our own order id back to the stored payment.
        """
        from subscriptions.models import Payment
        from subscriptions.views.params import param

        payment_id = param(request, *self.tran_ref_params)
        if payment_id:
            return str(payment_id)

        order_id = param(request, "order_id", "orderId")
        if not order_id:
            return None

        payment = (
            Payment.objects.filter(session_meta__order_id=str(order_id))
            .order_by("-created_at")
            .first()
        )
        if payment is None:
            logger.warning("Al Qaseh: no payment found for order_id=%s", order_id)
            return None
        return payment.tran_ref or None

    # -- credentials ------------------------------------------------------

    def test_credentials(self, config: dict) -> dict:
        client_id = (config.get("clientId") or "").strip()
        client_secret = (config.get("clientSecret") or "").strip()
        if not client_id or not client_secret:
            return {
                "success": False,
                "message": "Client ID and Client Secret are required",
            }
        # Payment history is the cheapest authenticated read; a 401 means the
        # credentials are wrong, anything else means they authenticated.
        try:
            self._request(
                "GET", "/egw/payments/history", config, params={"limit": 1}
            )
        except GatewayError as exc:
            return {"success": False, "message": exc.message}
        return {"success": True, "message": "Credentials are valid"}


register(AlqasehAdapter())
