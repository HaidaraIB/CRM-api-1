import json
import logging
from decimal import Decimal

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from crm_saas_api.responses import error_response, success_response, validation_error_response
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from ..models import (
    Plan,
    Subscription,
    Payment,
    PaymentStatus,
)
from ..serializers import CreateFibPaymentSerializer
from ..fib_utils import get_fib_gateway, create_fib_payment_session
from ..services.billing import finalize_completed_payment, resolve_checkout_pricing
from ..phone_verification_gate import require_owner_phone_verified

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([AllowAny])
def create_fib_payment(request):
    """
    Create a FIB (First Iraqi Bank) payment session for a subscription.
    Returns QR code and app links; user pays via FIB app. No redirect.
    POST /api/payments/create-fib-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: 'monthly' | 'yearly' }
    """
    serializer = CreateFibPaymentSerializer(data=request.data)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)

    subscription_id = serializer.validated_data.get("subscription_id")
    plan_id = serializer.validated_data.get("plan_id")
    billing_cycle_param = serializer.validated_data.get("billing_cycle")

    if not subscription_id:
        return error_response(
            "subscription_id is required",
            code="bad_request",
        )

    try:
        subscription = Subscription.objects.select_related("plan", "company__owner").get(
            id=subscription_id
        )
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    gate = require_owner_phone_verified(subscription)
    if gate is not None:
        return gate

    try:
        target_plan, billing_cycle, amount_dec, intent = resolve_checkout_pricing(
            subscription,
            target_plan_id=plan_id,
            billing_cycle_param=billing_cycle_param,
        )
    except ValueError as e:
        return error_response(str(e), code="invalid_checkout")
    except Plan.DoesNotExist:
        return error_response(
            "Plan not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return error_response(
            "Subscription is already active. Use renewal or plan change to proceed.",
            code="bad_request",
        )

    amount = float(amount_dec)
    if amount <= 0:
        return error_response(
            "Plan is free, no payment required",
            code="bad_request",
        )

    fib_gateway = get_fib_gateway()
    if not fib_gateway:
        return error_response(
            "FIB payment gateway is not configured or enabled",
            code="bad_request",
        )

    callback_url = f"{settings.API_BASE_URL}/api/payments/fib-callback/"
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username
    description = f"Subscription {subscription_id}"[:50]

    try:
        result = create_fib_payment_session(
            amount=amount,
            customer_email=subscription.company.owner.email,
            customer_name=user_name,
            subscription_id=str(subscription_id),
            callback_url=callback_url,
            description=description,
            expires_in=900,
        )
    except ValueError as e:
        logger.error("FIB create session error: %s", e)
        return error_response(str(e), code="fib_session_error")
    except Exception as e:
        logger.error("FIB create session error: %s", e, exc_info=True)
        return error_response(str(e), code="fib_session_error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    from decimal import Decimal
    payment_id = result.get("paymentId")
    payment = Payment.objects.create(
        subscription=subscription,
        amount=amount,
        currency="USD",
        exchange_rate=Decimal("1"),
        amount_usd=Decimal(str(amount)),
        payment_method=fib_gateway,
        payment_status=PaymentStatus.PENDING.value,
        tran_ref=payment_id,
        target_plan=target_plan,
        billing_cycle=billing_cycle,
    )
    logger.info(
        "Created FIB payment record: payment_id=%s, fib_payment_id=%s, subscription_id=%s",
        payment.id, payment_id, subscription_id,
    )

    return success_response(
        data={
            "payment_id": payment_id,
            "subscription_id": subscription_id,
            "redirect_url": None,
            "qr_code": result.get("qrCode"),
            "readable_code": result.get("readableCode"),
            "business_app_link": result.get("businessAppLink"),
            "corporate_app_link": result.get("corporateAppLink"),
            "personal_app_link": result.get("personalAppLink"),
            "valid_until": result.get("validUntil"),
        },
    )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def fib_callback(request):
    """
    FIB server-to-server callback when payment status changes.
    POST body: { "id": paymentId, "status": "PAID" | "UNPAID" | "DECLINED" }
    """
    logger.info("FIB callback received: method=%s, body=%s", request.method, request.body)
    try:
        if hasattr(request, "data") and request.data:
            payload = request.data
        else:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception as e:
        logger.error("FIB callback parse error: %s", e)
        return error_response("Invalid JSON", code="invalid_json")

    payment_id = payload.get("id")
    fib_status = (payload.get("status") or "").upper()
    if not payment_id:
        return error_response("Missing id", code="missing_field")

    payment = Payment.objects.filter(tran_ref=payment_id).order_by("-created_at").first()
    if not payment:
        logger.warning("FIB callback: payment not found for id=%s", payment_id)
        return error_response("Payment not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)

    subscription = payment.subscription
    subscription_id = subscription.id
    amount = float(payment.amount)

    if fib_status == "PAID":
        payment_was_completed = payment.payment_status == PaymentStatus.COMPLETED.value
        if not payment_was_completed:
            payment.payment_status = PaymentStatus.COMPLETED.value
            payment.save(update_fields=["payment_status", "updated_at"])

        pay_usd = float(payment.amount_usd) if payment.amount_usd is not None else float(payment.amount)
        if not payment_was_completed:
            try:
                finalize_completed_payment(subscription, payment, pay_usd)
                subscription.refresh_from_db()
            except ValueError as err:
                logger.error("FIB billing apply failed: %s", err, exc_info=True)
                return error_response(str(err), code="billing_error", status_code=status.HTTP_400_BAD_REQUEST)

        logger.info(
            "FIB payment PAID: subscription_id=%s, amount_usd=%s, end_date=%s",
            subscription_id,
            pay_usd,
            subscription.end_date,
        )
    elif fib_status == "DECLINED":
        payment.payment_status = PaymentStatus.FAILED.value
        payment.save(update_fields=["payment_status", "updated_at"])
        logger.info("FIB payment DECLINED: payment_id=%s", payment.id)
    else:
        logger.info("FIB callback status=%s for payment_id=%s, no change", fib_status, payment_id)

    return success_response(message="OK")
