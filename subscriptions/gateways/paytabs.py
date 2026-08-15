"""PayTabs adapter."""
from __future__ import annotations

import logging

from django.conf import settings

from subscriptions.gateways.base import (
    CARD_GROUP,
    BaseGatewayAdapter,
    CheckoutContext,
    CheckoutSession,
    GatewayError,
    GatewayResult,
)
from subscriptions.gateways.registry import register

logger = logging.getLogger(__name__)

# PayTabs response_status codes.
_PAID = {"A"}                       # Authorised
_FAILED = {"D", "E", "C", "V"}      # Declined, Error, Cancelled, Voided
# H (Hold) and P (Pending) fall through to "pending".


class PaytabsAdapter(BaseGatewayAdapter):
    slug = "paytabs"
    name_aliases = ("paytabs", "pay tabs")
    tran_ref_params = ("tran_ref", "tranRef")
    exclusive_group = CARD_GROUP

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        from subscriptions.paytabs_utils import create_paytabs_payment_session

        return_url = ctx.return_url or (
            f"{settings.PAYTABS_RETURN_URL}?subscription_id={ctx.subscription_id}"
        )
        callback_url = ctx.callback_url or getattr(
            settings, "PAYTABS_CALLBACK_URL", ""
        ) or f"{settings.API_BASE_URL}/api/payments/paytabs-callback/"

        try:
            result = create_paytabs_payment_session(
                amount=float(ctx.amount_usd),
                customer_email=ctx.customer_email,
                customer_name=ctx.customer_name,
                customer_phone=ctx.customer_phone,
                subscription_id=ctx.subscription_id,
                return_url=return_url,
                callback_url=callback_url,
            )
        except Exception as exc:
            raise GatewayError(f"Paytabs API error: {exc}") from exc

        redirect_url = result.get("redirect_url")
        if not redirect_url:
            raise GatewayError(
                result.get("message")
                or result.get("error")
                or "Failed to create payment session"
            )
        return CheckoutSession(
            tran_ref=result.get("tran_ref", ""), checkout_url=redirect_url
        )

    def verify(self, tran_ref: str) -> GatewayResult:
        from subscriptions.paytabs_utils import verify_paytabs_payment

        result = verify_paytabs_payment(tran_ref) or {}
        code = (result.get("payment_result") or {}).get("response_status")
        if code in _PAID:
            return GatewayResult("paid", result)
        if code in _FAILED:
            return GatewayResult("failed", result)
        return GatewayResult("pending", result)


register(PaytabsAdapter())
