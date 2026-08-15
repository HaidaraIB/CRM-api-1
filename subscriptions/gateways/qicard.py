"""QiCard adapter."""
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

_PAID = {"SUCCESS"}
_FAILED = {"FAILED", "AUTHENTICATION_FAILED", "CANCELLED", "EXPIRED"}


class QicardAdapter(BaseGatewayAdapter):
    slug = "qicard"
    name_aliases = ("qicard", "qi card", "qi-card")
    tran_ref_params = ("paymentId", "payment_id")

    def create_session(self, ctx: CheckoutContext) -> CheckoutSession:
        from subscriptions.qicard_utils import create_qicard_payment_session

        try:
            result = create_qicard_payment_session(
                amount=float(ctx.amount_usd),
                customer_email=ctx.customer_email,
                customer_name=ctx.customer_name,
                customer_phone=ctx.customer_phone,
                subscription_id=ctx.subscription_id,
                return_url=ctx.return_url,
                notification_url=ctx.callback_url,
            )
        except Exception as exc:
            raise GatewayError(f"QiCard API error: {exc}") from exc

        payment_id = result.get("payment_id") or result.get("paymentId")
        form_url = result.get("form_url") or result.get("formUrl")
        if not payment_id or not form_url:
            raise GatewayError("QiCard did not return a payment session")
        return CheckoutSession(
            tran_ref=str(payment_id),
            checkout_url=form_url,
            meta={"request_id": result.get("request_id")},
        )

    def verify(self, tran_ref: str) -> GatewayResult:
        from subscriptions.qicard_utils import verify_qicard_payment

        result = verify_qicard_payment(tran_ref) or {}
        status_value = (result.get("status") or "").upper()
        if status_value in _PAID:
            return GatewayResult("paid", result)
        if status_value in _FAILED:
            return GatewayResult("failed", result)
        return GatewayResult("pending", result)

    def session_payload(self, payment, session: CheckoutSession) -> dict:
        return {
            "redirect_url": session.checkout_url,
            "payment_id": session.tran_ref,
            "request_id": (session.meta or {}).get("request_id"),
        }

    def test_credentials(self, config: dict) -> dict:
        from subscriptions.qicard_utils import test_qicard_credentials

        terminal_id = config.get("terminalId", "")
        username = config.get("username", "")
        password = config.get("password", "")
        if not terminal_id or not username or not password:
            return {
                "success": False,
                "message": "Terminal ID, Username, and Password are required",
            }
        return test_qicard_credentials(
            terminal_id, username, password, config.get("environment", "test")
        )


register(QicardAdapter())
