import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response

from ..models import Payment
from ..zaincash_utils import get_zaincash_gateway
from ..gateways.registry import get_adapter
from ..services.payment_completion import (
    confirm_and_finalize_payment,
    find_payment_by_tran_ref,
)
from ..services.redirects import GENERIC_ERROR_MESSAGE, payment_redirect
from .checkout import create_checkout_session
from .params import int_or_none, param

logger = logging.getLogger(__name__)


def _is_api_call(request):
    """
    True when the frontend called this endpoint itself (POST JSON) rather than
    Zain Cash redirecting the browser here - those callers want JSON, not a 302.
    """
    if request.method != "POST":
        return False
    data = getattr(request, "data", None)
    if not isinstance(data, dict) or not data:
        return False
    return "token" in data or "subscription_id" in data


def _latest_zaincash_payment(subscription_id):
    gateway = get_zaincash_gateway()
    if not gateway or not subscription_id:
        return None
    return (
        Payment.objects.filter(
            subscription_id=subscription_id, payment_method=gateway
        )
        .select_related("subscription", "payment_method", "target_plan")
        .order_by("-created_at")
        .first()
    )


def _zaincash_response(request, *, ok, subscription_id, message=None, status_label):
    """Zain Cash return answers JSON to the frontend and a 302 to the browser."""
    if _is_api_call(request):
        if ok:
            return success_response(
                data={
                    "status": status_label,
                    "message": message or "Payment completed successfully",
                    "subscription_id": subscription_id,
                }
            )
        return error_response(
            message or "Payment failed",
            code="payment_failed",
            details={
                "gateway_status": status_label,
                "subscription_id": subscription_id,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return payment_redirect(
        status=status_label, subscription_id=subscription_id, message=message
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_zaincash_payment(request):
    """
    Open a zaincash checkout session for a subscription.
    POST /api/payments/create-zaincash-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: str }

    Thin wrapper so the URL is unchanged; the flow lives in views/checkout.py.
    """
    return create_checkout_session(request, slug="zaincash")


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def zaincash_return(request):
    """
    Zain Cash return. Confirms with the Zain Cash API before finalizing.

    GET  /api/payments/zaincash-return/?token=<jwt>&subscription_id=<id>
    POST /api/payments/zaincash-return/  { token, subscription_id }  -> JSON

    The return token is signed with the merchant secret and carries the
    transaction id, but its `status` claim is not treated as proof of payment:
    the payment is applied through confirm_and_finalize_payment, which re-queries
    Zain Cash under a row lock. tran_ref keeps holding the transaction id (never
    the token), so later re-queries still resolve.
    """
    subscription_id = int_or_none(param(request, "subscription_id"))
    # The adapter unwraps the signed token to the transaction id that
    # tran_ref and the Zain Cash status API are both keyed by.
    transaction_id = get_adapter("zaincash").extract_tran_ref(request)

    payment = find_payment_by_tran_ref(str(transaction_id)) if transaction_id else None
    if payment is None:
        payment = _latest_zaincash_payment(subscription_id)

    if payment is None:
        logger.warning(
            "Zain Cash return: no payment for transaction_id=%s subscription_id=%s",
            transaction_id,
            subscription_id,
        )
        return _zaincash_response(
            request,
            ok=False,
            subscription_id=subscription_id,
            message="Missing transaction token",
            status_label="failed",
        )

    subscription_id = payment.subscription_id
    try:
        applied, reason = confirm_and_finalize_payment(payment, mark_failed=True)
    except ValueError as err:
        logger.error("Billing apply failed (ZainCash): %s", err, exc_info=True)
        return _zaincash_response(
            request,
            ok=False,
            subscription_id=subscription_id,
            message=str(err),
            status_label="failed",
        )
    except Exception:
        logger.exception("Error processing Zain Cash return payment_id=%s", payment.id)
        return _zaincash_response(
            request,
            ok=False,
            subscription_id=subscription_id,
            message=GENERIC_ERROR_MESSAGE,
            status_label="error",
        )

    logger.info(
        "Zain Cash return payment=%s applied=%s reason=%s", payment.id, applied, reason
    )
    if applied:
        return _zaincash_response(
            request,
            ok=True,
            subscription_id=subscription_id,
            status_label="success",
        )
    return _zaincash_response(
        request,
        ok=False,
        subscription_id=subscription_id,
        message="Payment failed",
        status_label="failed",
    )
