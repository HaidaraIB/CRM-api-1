"""
Zain Cash adapter.

Zain Cash is the odd one out: it redirects with a JWT signed by the merchant
secret, but its status API is keyed by the *transaction id* carried inside that
token. `tran_ref` therefore always holds the transaction id, and the token is
decoded on the way in - storing the token there (as the old return handler did)
broke every later re-query.
"""
from __future__ import annotations

import logging

from django.conf import settings

from subscriptions.gateways.base import (
    BaseGatewayAdapter,
    CheckoutContext,
    CheckoutSession,
    GatewayError,
    GatewayResult,
)
from subscriptions.gateways.registry import register

logger = logging.getLogger(__name__)

_PAID = {"success", "completed"}
_FAILED = {"failed", "cancelled", "canceled", "expired", "rejected"}


class ZaincashAdapter(BaseGatewayAdapter):
    slug = "zaincash"
    name_aliases = ("zaincash", "zain cash", "zain-cash", "zain")
    tran_ref_params = ("token", "id", "transactionToken", "jwt")

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        from subscriptions.zaincash_utils import create_zaincash_payment_session

        return_url = ctx.return_url or (
            f"{settings.FRONTEND_URL}/payment/success"
            f"?subscription_id={ctx.subscription_id}"
        )
        try:
            result = create_zaincash_payment_session(
                amount=float(ctx.amount_usd),
                customer_email=ctx.customer_email,
                customer_name=ctx.customer_name,
                customer_phone=ctx.customer_phone,
                subscription_id=ctx.subscription_id,
                return_url=return_url,
            )
        except Exception as exc:
            raise GatewayError(f"Zain Cash API error: {exc}") from exc

        transaction_id = result.get("id") or result.get("transaction_id")
        if not transaction_id:
            raise GatewayError(
                "Failed to create payment session: No transaction ID received"
            )

        payment_url = result.get("payment_url")
        if not payment_url:
            from subscriptions.gateways.registry import find_gateway_row

            row = find_gateway_row(self.slug)
            environment = ((row.config if row else None) or {}).get(
                "environment", "test"
            )
            base = (
                "https://api.zaincash.iq"
                if environment == "live"
                else "https://test.zaincash.iq"
            )
            payment_url = f"{base}/transaction/pay?id={transaction_id}"

        return CheckoutSession(tran_ref=str(transaction_id), checkout_url=payment_url)

    def session_payload(self, payment, session: CheckoutSession) -> dict:
        return {
            "redirect_url": session.checkout_url,
            "transaction_id": session.tran_ref,
        }

    def decode_token(self, token: str) -> dict:
        """Claims from the signed return token, or {} if it does not verify."""
        from subscriptions.zaincash_utils import verify_zaincash_payment

        try:
            return verify_zaincash_payment(token) or {}
        except Exception:
            logger.exception("Zain Cash token could not be verified")
            return {}

    def extract_tran_ref(self, request):
        from subscriptions.views.params import param

        value = param(request, *self.tran_ref_params)
        if not value:
            return None
        value = str(value)
        # A JWT (three dot-separated segments) carries the id; anything else
        # already is the transaction id.
        if value.count(".") != 2:
            return value
        return self.decode_token(value).get("id")

    def verify(self, tran_ref: str) -> GatewayResult:
        from subscriptions.zaincash_utils import check_zaincash_payment_status

        result = check_zaincash_payment_status(tran_ref) or {}
        status_value = (result.get("status") or "").lower()
        if status_value in _PAID:
            return GatewayResult("paid", result)
        if status_value in _FAILED:
            return GatewayResult("failed", result)
        return GatewayResult("pending", result)

    def test_credentials(self, config: dict) -> dict:
        from subscriptions.zaincash_utils import test_zaincash_credentials

        merchant_id = config.get("merchantId", "")
        merchant_secret = config.get("merchantSecret", "")
        if not merchant_id or not merchant_secret:
            return {
                "success": False,
                "message": "Merchant ID and Merchant Secret are required",
            }
        return test_zaincash_credentials(
            merchant_id,
            merchant_secret,
            config.get("environment", "test"),
            config.get("msisdn", ""),
        )


register(ZaincashAdapter())
