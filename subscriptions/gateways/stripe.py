"""Stripe adapter (one-shot hosted Checkout, never Stripe Subscriptions)."""
from __future__ import annotations

import logging

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


class StripeAdapter(BaseGatewayAdapter):
    slug = "stripe"
    name_aliases = ("stripe",)
    tran_ref_params = ("session_id", "sessionId")
    exclusive_group = CARD_GROUP

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        from subscriptions.stripe_utils import create_stripe_payment_session

        try:
            result = create_stripe_payment_session(
                amount=float(ctx.amount_usd),
                customer_email=ctx.customer_email,
                customer_name=ctx.customer_name,
                subscription_id=ctx.subscription_id,
                return_url=ctx.return_url,
                success_url=ctx.return_url,
                cancel_url=ctx.callback_url or ctx.return_url,
                extra_metadata={
                    "plan_id": ctx.target_plan.id,
                    "billing_cycle": ctx.billing_cycle,
                },
            )
        except Exception as exc:
            raise GatewayError(f"Stripe API error: {exc}") from exc

        session_id = result.get("session_id")
        if not session_id:
            raise GatewayError("Stripe did not return a session id")
        return CheckoutSession(
            tran_ref=session_id,
            checkout_url=result.get("url") or "",
            meta={"payment_intent": result.get("payment_intent")},
        )

    def verify(self, tran_ref: str) -> GatewayResult:
        from subscriptions.stripe_utils import verify_stripe_payment

        result = verify_stripe_payment(tran_ref) or {}
        stripe_status = (result.get("stripe_payment_status") or "").lower()
        payment_status = (result.get("payment_status") or "").lower()

        if stripe_status in ("paid", "no_payment_required") or payment_status == "completed":
            return GatewayResult("paid", result)
        # A Checkout Session that was never paid stays open until it expires;
        # Stripe reports no terminal "declined" state here, so this is pending.
        return GatewayResult("pending", result)

    def session_payload(self, payment, session: CheckoutSession) -> dict:
        return {
            "payment_id": payment.id,
            "redirect_url": session.checkout_url,
            "session_id": session.tran_ref,
        }

    def test_credentials(self, config: dict) -> dict:
        from subscriptions.stripe_utils import test_stripe_credentials

        secret_key = (config.get("secretKey") or "").strip()
        if not secret_key:
            return {"success": False, "message": "Secret Key is required"}
        return test_stripe_credentials(secret_key, config.get("publishableKey", ""))


register(StripeAdapter())
