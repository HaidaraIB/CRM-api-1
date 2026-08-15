import json
import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from crm_saas_api.responses import error_response, success_response
from django.views.decorators.csrf import csrf_exempt

from ..services.payment_completion import (
    confirm_and_finalize_payment,
    find_payment_by_tran_ref,
)
from .checkout import create_checkout_session

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_fib_payment(request):
    """
    Open a fib checkout session for a subscription.
    POST /api/payments/create-fib-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: str }

    Thin wrapper so the URL is unchanged; the flow lives in views/checkout.py.
    """
    return create_checkout_session(request, slug="fib")


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def fib_callback(request):
    """
    FIB server-to-server callback. Payload is a hint; status is confirmed via FIB API.
    POST body: { "id": paymentId, "status": "PAID" | "UNPAID" | "DECLINED" }
    """
    logger.info("FIB callback received: method=%s", request.method)
    try:
        if hasattr(request, "data") and request.data:
            payload = request.data
        else:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception as e:
        logger.error("FIB callback parse error: %s", e)
        return error_response("Invalid JSON", code="invalid_json")

    payment_id = payload.get("id")
    if not payment_id:
        return error_response("Missing id", code="missing_field")

    payment = find_payment_by_tran_ref(str(payment_id))
    if not payment:
        logger.warning("FIB callback: payment not found for id=%s", payment_id)
        return error_response("Payment not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)

    ok, reason = confirm_and_finalize_payment(payment, mark_failed=True)
    logger.info("FIB callback payment=%s ok=%s reason=%s", payment.id, ok, reason)
    return success_response(message="OK", data={"ok": ok, "reason": reason})
