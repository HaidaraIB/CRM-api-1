import json
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response

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
def create_qicard_payment(request):
    """
    Open a qicard checkout session for a subscription.
    POST /api/payments/create-qicard-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: str }

    Thin wrapper so the URL is unchanged; the flow lives in views/checkout.py.
    """
    return create_checkout_session(request, slug="qicard")


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def qicard_return(request):
    """
    QiCard browser return. Confirms with the QiCard API before finalizing.

    GET/POST /api/payments/qicard-return/?paymentId=<id>&subscription_id=<id>

    The `status` query param QiCard appends is a hint only: the payment is
    located by its gateway reference and applied through
    confirm_and_finalize_payment, which re-queries QiCard under a row lock so
    this path and the webhook cannot double-apply.
    """
    payment_id = param(request, "paymentId", "payment_id")
    subscription_id = int_or_none(param(request, "subscription_id"))

    if not payment_id:
        logger.warning("QiCard return: missing paymentId")
        return payment_redirect(
            status="failed",
            subscription_id=subscription_id,
            message="Missing payment ID",
        )

    payment = find_payment_by_tran_ref(str(payment_id))
    if payment is None:
        logger.warning(
            "QiCard return: no payment for paymentId=%s subscription_id=%s",
            payment_id,
            subscription_id,
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
        logger.error("Billing apply failed (QiCard): %s", err, exc_info=True)
        return payment_redirect(
            status="failed", subscription_id=subscription_id, message=str(err)
        )
    except Exception:
        logger.exception("Error processing QiCard return payment_id=%s", payment.id)
        return payment_redirect(
            status="error",
            subscription_id=subscription_id,
            message=GENERIC_ERROR_MESSAGE,
        )

    logger.info(
        "QiCard return payment=%s applied=%s reason=%s", payment.id, applied, reason
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
def qicard_webhook(request):
    """
    Handle QiCard webhook notifications.
    Payload is a hint only — status is confirmed via QiCard API before finalize.
    POST /api/payments/qicard-webhook/
    """
    logger.info("QiCard webhook called method=%s", request.method)

    try:
        if hasattr(request, "data") and request.data:
            payload = request.data
        else:
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                payload = {}

        payment_id = payload.get("paymentId")
        if not payment_id:
            logger.error("Missing paymentId in QiCard webhook")
            return error_response(
                "Missing paymentId",
                code="bad_request",
            )

        payment = find_payment_by_tran_ref(str(payment_id))
        if not payment:
            logger.warning("Payment not found for QiCard payment_id: %s", payment_id)
            return error_response(
                "Payment not found",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        ok, reason = confirm_and_finalize_payment(payment, mark_failed=True)
        logger.info("QiCard webhook payment=%s ok=%s reason=%s", payment.id, ok, reason)
        return success_response(message="OK", data={"ok": ok, "reason": reason})

    except Exception as e:
        logger.error("ERROR processing QiCard webhook: %s", e, exc_info=True)
        return error_response(
            str(e),
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
