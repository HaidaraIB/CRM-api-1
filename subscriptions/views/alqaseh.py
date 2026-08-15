"""
Al Qaseh endpoints.

All three are thin: the checkout flow lives in views/checkout.py and the
gateway specifics live in gateways/alqaseh.py. Neither the redirect nor the
webhook payload is trusted - both resolve the payment and hand it to
confirm_and_finalize_payment, which re-queries Al Qaseh under a row lock.
"""
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response

from ..gateways.registry import get_adapter
from ..services.payment_completion import (
    confirm_and_finalize_payment,
    find_payment_by_tran_ref,
)
from ..services.redirects import GENERIC_ERROR_MESSAGE, payment_redirect
from .checkout import create_checkout_session
from .params import int_or_none, param

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_alqaseh_payment(request):
    """
    Open an Al Qaseh checkout session for a subscription.
    POST /api/payments/create-alqaseh-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: str }
    """
    return create_checkout_session(request, slug="alqaseh")


def _resolve_payment(request):
    """Locate the Payment from whichever reference Al Qaseh sent."""
    tran_ref = get_adapter("alqaseh").extract_tran_ref(request)
    if not tran_ref:
        return None
    return find_payment_by_tran_ref(str(tran_ref))


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def alqaseh_return(request):
    """
    Al Qaseh browser return.
    GET/POST /api/payments/alqaseh-return/?payment_id=<id>&order_id=<id>&status=<s>

    The `status` query param is a hint only; the API is re-queried before any
    period is applied.
    """
    subscription_id = int_or_none(param(request, "subscription_id"))

    payment = _resolve_payment(request)
    if payment is None:
        logger.warning(
            "Al Qaseh return: no payment for payment_id=%s order_id=%s",
            param(request, "payment_id", "paymentId"),
            param(request, "order_id", "orderId"),
        )
        return payment_redirect(
            status="failed",
            subscription_id=subscription_id,
            message="Could not link payment to subscription",
        )

    subscription_id = payment.subscription_id
    try:
        applied, reason = confirm_and_finalize_payment(payment, mark_failed=True)
    except ValueError as err:
        logger.error("Billing apply failed (Al Qaseh): %s", err, exc_info=True)
        return payment_redirect(
            status="failed", subscription_id=subscription_id, message=str(err)
        )
    except Exception:
        logger.exception("Error processing Al Qaseh return payment_id=%s", payment.id)
        return payment_redirect(
            status="error",
            subscription_id=subscription_id,
            message=GENERIC_ERROR_MESSAGE,
        )

    logger.info(
        "Al Qaseh return payment=%s applied=%s reason=%s", payment.id, applied, reason
    )
    if applied:
        return payment_redirect(status="success", subscription_id=subscription_id)
    if reason == "marked_failed":
        return payment_redirect(
            status="failed", subscription_id=subscription_id, message="Payment failed"
        )
    return payment_redirect(
        status="pending",
        subscription_id=subscription_id,
        message="Payment is still pending",
    )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def alqaseh_webhook(request):
    """
    Al Qaseh server-to-server webhook.
    POST /api/payments/alqaseh-webhook/

    The payload identifies the transaction by `order_id` (it carries no
    payment_id), and it is a hint only - status is confirmed via the Al Qaseh
    API before finalize.

    TODO(alqaseh-spec): the payload includes `p_sign`, `nonce` and `timestamp`,
    but Al Qaseh does not publish how p_sign is computed. Verifying it would be
    defence in depth; it is not load-bearing here, because nothing is applied
    without the server-side re-query below.
    """
    logger.info("Al Qaseh webhook received method=%s", request.method)

    payment = _resolve_payment(request)
    if payment is None:
        logger.warning(
            "Al Qaseh webhook: payment not found for order_id=%s",
            param(request, "order_id", "orderId"),
        )
        return error_response(
            "Payment not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    ok, reason = confirm_and_finalize_payment(payment, mark_failed=True)
    logger.info("Al Qaseh webhook payment=%s ok=%s reason=%s", payment.id, ok, reason)
    return success_response(message="OK", data={"ok": ok, "reason": reason})
