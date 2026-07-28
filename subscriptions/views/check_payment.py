import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from crm_saas_api.responses import error_response, success_response

from ..models import Subscription, Payment, PaymentStatus
from ..paytabs_utils import verify_paytabs_payment
from ..stripe_utils import verify_stripe_payment
from ..zaincash_utils import check_zaincash_payment_status
from ..fib_utils import check_fib_payment_status
from ..services.billing import finalize_completed_payment
from ..services.checkout_auth import require_subscription_owner
from ..services.subscription_helpers import (
    _payment_amount_usd,
    reconcile_unapplied_completed_payment,
)

logger = logging.getLogger(__name__)


def _mark_completed_and_finalize(subscription, payment):
    """Mark payment completed (if needed) and apply period rules idempotently."""
    if payment.payment_status != PaymentStatus.COMPLETED.value:
        payment.payment_status = PaymentStatus.COMPLETED.value
        payment.save(update_fields=["payment_status", "updated_at"])
    if payment.applied_at is None:
        finalize_completed_payment(subscription, payment, _payment_amount_usd(payment))
        subscription.refresh_from_db()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def check_payment_status(request, subscription_id):
    """
    Check payment status by subscription_id - for frontend polling
    GET /api/payment-status/<subscription_id>/
    Returns payment status and subscription status
    """
    try:
        subscription = Subscription.objects.select_related("company__owner").get(id=subscription_id)
        owner_err = require_subscription_owner(request, subscription)
        if owner_err is not None:
            return owner_err

        try:
            payment = (
                Payment.objects.filter(subscription=subscription)
                .order_by("-created_at")
                .first()
            )
        except Exception as e:
            logger.warning("Error fetching payment: %s", e)
            payment = None

        subscription.refresh_from_db()

        payment_status_value = payment.payment_status if payment else "pending"
        paytabs_status = None
        gateway_status = None

        # If subscription is already active, treat payment as completed for UI,
        # but still reconcile any unapplied completed payment below.
        if subscription.is_active:
            payment_status_value = PaymentStatus.COMPLETED.value
            paytabs_status = "A"
            gateway_status = "success"
        elif payment and payment.tran_ref:
            payment_gateway = payment.payment_method
            if payment_gateway:
                gateway_name = payment_gateway.name.lower()

                if "zain" in gateway_name or "zaincash" in gateway_name:
                    try:
                        result = check_zaincash_payment_status(payment.tran_ref)
                        gateway_status = result.get("status", "pending")
                        if gateway_status == "success":
                            payment_status_value = PaymentStatus.COMPLETED.value
                            _mark_completed_and_finalize(subscription, payment)
                    except Exception as e:
                        logger.warning("Could not verify Zain Cash payment: %s", e)
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"

                elif "paytabs" in gateway_name:
                    try:
                        result = verify_paytabs_payment(payment.tran_ref)
                        paytabs_status = result.get("payment_result", {}).get(
                            "response_status"
                        )
                        if paytabs_status == "A":
                            payment_status_value = PaymentStatus.COMPLETED.value
                            _mark_completed_and_finalize(subscription, payment)
                    except Exception as e:
                        logger.warning("Could not verify payment with PayTabs: %s", e)
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            paytabs_status = "A"

                elif "stripe" in gateway_name:
                    try:
                        result = verify_stripe_payment(payment.tran_ref)
                        stripe_status = result.get("stripe_payment_status")
                        if stripe_status == "paid":
                            gateway_status = "success"
                            payment_status_value = PaymentStatus.COMPLETED.value
                            _mark_completed_and_finalize(subscription, payment)
                    except Exception as e:
                        logger.warning("Could not verify Stripe payment: %s", e)
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"

                elif "fib" in gateway_name or "first iraqi" in gateway_name:
                    try:
                        result = check_fib_payment_status(payment.tran_ref)
                        fib_status = (result.get("status") or "").upper()
                        gateway_status = fib_status.lower() if fib_status else "pending"
                        if fib_status == "PAID":
                            payment_status_value = PaymentStatus.COMPLETED.value
                            _mark_completed_and_finalize(subscription, payment)
                            gateway_status = "success"
                        elif fib_status == "DECLINED":
                            payment_status_value = PaymentStatus.FAILED.value
                            if payment.payment_status != PaymentStatus.FAILED.value:
                                payment.payment_status = PaymentStatus.FAILED.value
                                payment.save(update_fields=["payment_status", "updated_at"])
                    except Exception as e:
                        logger.warning("Could not verify FIB payment: %s", e)
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"

            if (
                payment.payment_status == PaymentStatus.COMPLETED.value
                and not paytabs_status
                and not gateway_status
            ):
                paytabs_status = "A"
                gateway_status = "success"

        subscription.refresh_from_db()
        try:
            plan = subscription.plan
            is_free_or_trial = float(plan.price_monthly) <= 0 and float(plan.price_yearly) <= 0
            has_completed_payment = Payment.objects.filter(
                subscription=subscription,
                payment_status=PaymentStatus.COMPLETED.value,
            ).exists()
            if is_free_or_trial and not has_completed_payment:
                from datetime import timedelta

                if int(getattr(plan, "trial_days", 0) or 0) > 0:
                    computed_end = subscription.start_date + timedelta(days=int(plan.trial_days))
                else:
                    computed_end = subscription.start_date + timedelta(days=365 * 100)
                if subscription.end_date is None or abs((subscription.end_date - computed_end).days) >= 1:
                    subscription.end_date = computed_end
                    subscription.save(update_fields=["end_date", "updated_at"])
                    subscription.refresh_from_db()
            elif has_completed_payment:
                reconcile_unapplied_completed_payment(subscription)
                subscription.refresh_from_db()
        except Exception:
            # Never break status endpoint due to reconciliation
            pass

        is_truly_active = subscription.is_truly_active()
        days_until_expiry = subscription.days_until_expiry()
        is_expiring_soon = subscription.is_expiring_soon(days_threshold=30)

        if subscription.is_active and not is_truly_active:
            subscription.is_active = False
            subscription.save(update_fields=["is_active", "updated_at"])
            subscription.refresh_from_db()
            is_truly_active = False
            logger.info(
                "Subscription %s was marked as inactive due to expired end_date",
                subscription_id,
            )

        response_data = {
            "subscription_id": subscription_id,
            "subscription_active": bool(subscription.is_active),
            "is_truly_active": is_truly_active,
            "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
            "days_until_expiry": days_until_expiry,
            "is_expiring_soon": is_expiring_soon,
            "payment_status": payment_status_value,
            "paytabs_status": paytabs_status,
            "gateway_status": gateway_status,
            "payment_exists": payment is not None,
        }

        return success_response(data=response_data)
    except Payment.DoesNotExist:
        return error_response(
            "Payment not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Subscription.DoesNotExist:
        return error_response(
            "Subscription not found",
            code="not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except Exception as e:
        logger.error("Error checking payment status: %s", e, exc_info=True)
        return error_response(
            f"Error checking payment status: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
