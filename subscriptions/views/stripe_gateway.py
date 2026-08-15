import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response

from ..models import PaymentStatus
from ..stripe_utils import get_stripe_gateway
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
def create_stripe_payment(request):
    """
    Open a stripe checkout session for a subscription.
    POST /api/payments/create-stripe-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: str }

    Thin wrapper so the URL is unchanged; the flow lives in views/checkout.py.
    """
    return create_checkout_session(request, slug="stripe")


@csrf_exempt
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def stripe_return(request):
    """
    Stripe browser return. Confirms via Checkout Session re-query before finalizing.

    GET/POST /api/payments/stripe-return/?session_id=<id>&subscription_id=<id>

    The payment is located by its session id and applied through
    confirm_and_finalize_payment, which re-queries Stripe under a row lock so
    this path and the webhook cannot double-apply.
    """
    session_id = param(request, "session_id")
    subscription_id = int_or_none(param(request, "subscription_id"))

    if not session_id:
        logger.warning("Stripe return: missing session_id")
        return payment_redirect(
            status="failed",
            subscription_id=subscription_id,
            message="Missing session ID",
        )

    payment = find_payment_by_tran_ref(session_id)
    if payment is None:
        logger.warning(
            "Stripe return: no payment for session_id=%s subscription_id=%s",
            session_id,
            subscription_id,
        )
        return payment_redirect(
            status="failed",
            subscription_id=subscription_id,
            message="Invalid transaction",
        )

    subscription_id = payment.subscription_id
    try:
        applied, reason = confirm_and_finalize_payment(payment, mark_failed=True)
    except ValueError as err:
        logger.error("Billing apply failed (Stripe): %s", err, exc_info=True)
        return payment_redirect(
            status="failed", subscription_id=subscription_id, message=str(err)
        )
    except Exception:
        logger.exception("Error processing Stripe return payment_id=%s", payment.id)
        return payment_redirect(
            status="error",
            subscription_id=subscription_id,
            message=GENERIC_ERROR_MESSAGE,
        )

    logger.info(
        "Stripe return payment=%s applied=%s reason=%s", payment.id, applied, reason
    )
    if applied:
        return payment_redirect(
            status="success", subscription_id=subscription_id, session_id=session_id
        )
    return payment_redirect(
        status="failed", subscription_id=subscription_id, message="Payment failed"
    )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def stripe_webhook(request):
    """
    Stripe server webhook. Verifies signature then finalizes via Session re-query.
    POST /api/payments/stripe-webhook/
    """
    import stripe

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    gateway = get_stripe_gateway()
    if not gateway:
        return error_response("Stripe gateway not configured", code="bad_request")

    config = gateway.config or {}
    webhook_secret = (config.get("webhookSecret") or "").strip()
    secret_key = (config.get("secretKey") or "").strip()
    if not webhook_secret or not secret_key:
        logger.error("Stripe webhookSecret or secretKey missing")
        return error_response(
            "Stripe webhook not configured",
            code="bad_request",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    stripe.api_key = secret_key
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("Stripe webhook invalid payload")
        return error_response("Invalid payload", code="bad_request")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        return error_response(
            "Invalid signature",
            code="invalid_signature",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    event_type = event.get("type") or ""
    data_object = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        session_id = data_object.get("id")
        payment = find_payment_by_tran_ref(session_id) if session_id else None
        if not payment:
            logger.warning("Stripe webhook: payment not found for session %s", session_id)
            return success_response(message="ignored")
        ok, reason = confirm_and_finalize_payment(payment)
        logger.info("Stripe webhook checkout.session.completed payment=%s result=%s", payment.id, reason)
        return success_response(data={"ok": ok, "reason": reason})

    if event_type in (
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    ):
        session_id = data_object.get("id")
        payment = find_payment_by_tran_ref(session_id) if session_id else None
        if payment and payment.payment_status == PaymentStatus.PENDING.value:
            payment.payment_status = PaymentStatus.FAILED.value
            payment.save(update_fields=["payment_status", "updated_at"])
            logger.info("Stripe webhook marked payment %s FAILED (%s)", payment.id, event_type)
        return success_response(message="OK")

    return success_response(message="ignored")

