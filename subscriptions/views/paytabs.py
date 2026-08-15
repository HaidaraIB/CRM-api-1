import json
import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response

from ..models import Payment
from ..paytabs_utils import get_paytabs_gateway
from ..services.payment_completion import (
    confirm_and_finalize_payment,
    find_payment_by_tran_ref,
)
from ..services.redirects import GENERIC_ERROR_MESSAGE, payment_redirect
from .checkout import create_checkout_session
from .params import int_or_none, param

logger = logging.getLogger(__name__)


def _latest_paytabs_payment(subscription_id):
    """Fallback for when PayTabs redirects with only subscription_id."""
    gateway = get_paytabs_gateway()
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_paytabs_payment(request):
    """
    Open a paytabs checkout session for a subscription.
    POST /api/payments/create-paytabs-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: str }

    Thin wrapper so the URL is unchanged; the flow lives in views/checkout.py.
    """
    return create_checkout_session(request, slug="paytabs")


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def paytabs_return(request):
    """
    PayTabs browser return. Confirms with the PayTabs query API before finalizing.

    GET/POST /api/payments/paytabs-return/?subscription_id=<id>&tran_ref=<ref>

    The redirect payload is a hint only: the payment is located by its gateway
    reference and applied through confirm_and_finalize_payment, which re-queries
    PayTabs under a row lock so this path and the callback cannot double-apply.
    """
    subscription_id = int_or_none(param(request, "subscription_id"))
    tran_ref = param(request, "tran_ref", "tranRef")

    payment = find_payment_by_tran_ref(tran_ref) if tran_ref else None
    if payment is None:
        payment = _latest_paytabs_payment(subscription_id)

    if payment is None:
        logger.warning(
            "PayTabs return: no payment found tran_ref=%s subscription_id=%s",
            tran_ref,
            subscription_id,
        )
        return payment_redirect(
            status="failed",
            subscription_id=subscription_id,
            message="Missing transaction reference",
        )

    subscription_id = payment.subscription_id
    try:
        applied, reason = confirm_and_finalize_payment(payment, mark_failed=True)
    except ValueError as err:
        logger.error("Billing apply failed (PayTabs): %s", err, exc_info=True)
        return payment_redirect(
            status="failed", subscription_id=subscription_id, message=str(err)
        )
    except Exception:
        logger.exception("Error processing PayTabs return payment_id=%s", payment.id)
        return payment_redirect(
            status="error",
            subscription_id=subscription_id,
            message=GENERIC_ERROR_MESSAGE,
        )

    logger.info(
        "PayTabs return payment=%s applied=%s reason=%s", payment.id, applied, reason
    )
    if applied:
        return payment_redirect(
            status="success",
            subscription_id=subscription_id,
            tranRef=payment.tran_ref,
        )
    return payment_redirect(
        status="failed", subscription_id=subscription_id, message="Payment failed"
    )


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def paytabs_callback(request):
    """
    PayTabs server-to-server callback (IPN). Confirm via query API before finalize.
    POST /api/payments/paytabs-callback/
    """
    logger.info("PayTabs callback received method=%s", request.method)
    data = {}
    try:
        if hasattr(request, "data") and request.data:
            data = request.data
        elif request.body:
            data = json.loads(request.body.decode("utf-8"))
    except Exception:
        data = {}

    # PayTabs may send form-encoded or JSON; also check query params
    tran_ref = (
        data.get("tran_ref")
        or data.get("tranRef")
        or request.GET.get("tran_ref")
        or request.GET.get("tranRef")
        or request.POST.get("tran_ref")
        or request.POST.get("tranRef")
    )
    if not tran_ref:
        logger.warning("PayTabs callback missing tran_ref")
        return error_response("Missing tran_ref", code="bad_request")

    payment = find_payment_by_tran_ref(str(tran_ref))
    if not payment:
        logger.warning("PayTabs callback: payment not found for tran_ref=%s", tran_ref)
        return error_response(
            "Payment not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    ok, reason = confirm_and_finalize_payment(payment)
    logger.info("PayTabs callback payment=%s ok=%s reason=%s", payment.id, ok, reason)
    return success_response(message="OK", data={"ok": ok, "reason": reason})

