import json
import logging
from datetime import timedelta
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
    Invoice,
    InvoiceStatus,
)
from ..serializers import CreateFibPaymentSerializer
from ..fib_utils import get_fib_gateway, create_fib_payment_session

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
        subscription = Subscription.objects.get(id=subscription_id)
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if plan_id:
        try:
            new_plan = Plan.objects.get(id=plan_id)
            subscription.plan = new_plan
            subscription.save(update_fields=["plan", "updated_at"])
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

    plan = subscription.plan
    if billing_cycle_param:
        billing_cycle = billing_cycle_param
    else:
        days_diff = (subscription.end_date - subscription.start_date).days
        billing_cycle = "yearly" if days_diff >= 330 else "monthly"

    amount = float(plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly)
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
        payment.payment_status = PaymentStatus.COMPLETED.value
        payment.save(update_fields=["payment_status", "updated_at"])

        plan = subscription.plan
        if amount > 0:
            if abs(amount - float(plan.price_yearly)) < 0.01:
                billing_cycle = "yearly"
            elif abs(amount - float(plan.price_monthly)) < 0.01:
                billing_cycle = "monthly"
            else:
                yearly_diff = abs(amount - float(plan.price_yearly))
                monthly_diff = abs(amount - float(plan.price_monthly))
                billing_cycle = "yearly" if yearly_diff < monthly_diff else "monthly"
        else:
            billing_cycle = "monthly"
            amount = float(plan.price_monthly)

        now = timezone.now()
        has_completed = Payment.objects.filter(
            subscription=subscription, payment_status=PaymentStatus.COMPLETED.value
        ).exclude(id=payment.id).exists()

        if has_completed and subscription.end_date and subscription.end_date > now:
            base_date = subscription.end_date
        elif has_completed and (not subscription.end_date or subscription.end_date <= now):
            base_date = now
        else:
            base_date = subscription.start_date if subscription.start_date and subscription.start_date > now else now

        new_end_date = base_date + (timedelta(days=365) if billing_cycle == "yearly" else timedelta(days=30))
        subscription.is_active = True
        subscription.end_date = new_end_date
        if not subscription.start_date or subscription.start_date <= now:
            subscription.start_date = now
        subscription.save(update_fields=["is_active", "end_date", "start_date", "updated_at"])

        Invoice.objects.create(
            subscription=subscription,
            amount=amount,
            due_date=new_end_date,
            status=InvoiceStatus.PAID.value,
        )
        logger.info(
            "FIB payment PAID: subscription_id=%s, amount=%s, billing_cycle=%s, end_date=%s",
            subscription_id, amount, billing_cycle, new_end_date,
        )
    elif fib_status == "DECLINED":
        payment.payment_status = PaymentStatus.FAILED.value
        payment.save(update_fields=["payment_status", "updated_at"])
        logger.info("FIB payment DECLINED: payment_id=%s", payment.id)
    else:
        logger.info("FIB callback status=%s for payment_id=%s, no change", fib_status, payment_id)

    return success_response(message="OK")
