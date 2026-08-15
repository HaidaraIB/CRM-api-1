"""First Iraqi Bank (FIB) adapter - QR flow, no browser redirect."""
from __future__ import annotations

import logging

from subscriptions.gateways.base import (
    BaseGatewayAdapter,
    CheckoutContext,
    CheckoutSession,
    GatewayError,
    GatewayResult,
)
from subscriptions.gateways.registry import register

logger = logging.getLogger(__name__)

_PAID = {"PAID"}
_FAILED = {"DECLINED", "CANCELLED", "EXPIRED"}


class FibAdapter(BaseGatewayAdapter):
    slug = "fib"
    name_aliases = ("fib", "first iraqi")
    tran_ref_params = ("id", "paymentId", "payment_id")

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        from subscriptions.fib_utils import create_fib_payment_session
        from subscriptions.services.payment_completion import parse_gateway_expiry

        try:
            result = create_fib_payment_session(
                amount=float(ctx.amount_usd),
                customer_email=ctx.customer_email,
                customer_name=ctx.customer_name,
                subscription_id=ctx.subscription_id,
                callback_url=ctx.callback_url,
            )
        except Exception as exc:
            raise GatewayError(f"FIB API error: {exc}") from exc

        payment_id = result.get("paymentId") or result.get("payment_id")
        if not payment_id:
            raise GatewayError("FIB did not return a payment id")
        return CheckoutSession(
            tran_ref=str(payment_id),
            # FIB is a QR flow: there is no hosted page to redirect to.
            checkout_url="",
            expires_at=parse_gateway_expiry(result.get("validUntil")),
            # Snake_case keys match what is already stored in session_meta on
            # existing rows, so reused sessions keep rendering.
            meta={
                "payment_id": payment_id,
                "qr_code": result.get("qrCode"),
                "readable_code": result.get("readableCode"),
                "business_app_link": result.get("businessAppLink"),
                "corporate_app_link": result.get("corporateAppLink"),
                "personal_app_link": result.get("personalAppLink"),
                "valid_until": result.get("validUntil"),
            },
        )

    def session_payload(self, payment, session: CheckoutSession) -> dict:
        meta = session.meta or {}
        return {
            "payment_id": session.tran_ref or meta.get("payment_id"),
            "subscription_id": payment.subscription_id,
            "redirect_url": None,
            "qr_code": meta.get("qr_code"),
            "readable_code": meta.get("readable_code"),
            "business_app_link": meta.get("business_app_link"),
            "corporate_app_link": meta.get("corporate_app_link"),
            "personal_app_link": meta.get("personal_app_link"),
            "valid_until": meta.get("valid_until"),
        }

    def verify(self, tran_ref: str) -> GatewayResult:
        from subscriptions.fib_utils import check_fib_payment_status

        result = check_fib_payment_status(tran_ref) or {}
        status_value = (result.get("status") or "").upper()
        if status_value in _PAID:
            return GatewayResult("paid", result)
        if status_value in _FAILED:
            return GatewayResult("failed", result)
        return GatewayResult("pending", result)

    def test_credentials(self, config: dict) -> dict:
        from subscriptions.fib_utils import test_fib_credentials

        client_id = (config.get("clientId") or "").strip()
        client_secret = (config.get("clientSecret") or "").strip()
        if not client_id or not client_secret:
            return {
                "success": False,
                "message": "Client ID and Client Secret are required",
            }
        return test_fib_credentials(
            client_id, client_secret, config.get("environment", "test")
        )


register(FibAdapter())
