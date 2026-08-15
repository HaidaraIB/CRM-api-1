import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from crm_saas_api.responses import error_response, success_response

from ..models import Subscription, Payment, PaymentStatus
from ..services.billing import finalize_completed_payment
from ..services.checkout_auth import require_subscription_company_member
from ..services.payment_completion import query_gateway_state
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
        # Status is read-only: any company member may poll (owners pay; staff need this after login).
        member_err = require_subscription_company_member(request, subscription)
        if member_err is not None:
            return member_err

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
            # One re-query for every gateway: the adapter normalizes its own
            # status vocabulary, so this endpoint no longer needs a branch per
            # gateway (and can no longer silently omit one, as it did QiCard).
            result = query_gateway_state(payment)

            if result.is_paid:
                gateway_status = "success"
                paytabs_status = "A"
                payment_status_value = PaymentStatus.COMPLETED.value
                _mark_completed_and_finalize(subscription, payment)
            elif result.is_failed:
                gateway_status = "failed"
                payment_status_value = PaymentStatus.FAILED.value
                if payment.payment_status != PaymentStatus.FAILED.value:
                    payment.payment_status = PaymentStatus.FAILED.value
                    payment.save(update_fields=["payment_status", "updated_at"])
            elif result.state == "pending":
                gateway_status = "pending"
            elif payment.payment_status == PaymentStatus.COMPLETED.value:
                # Gateway unreachable, but we already recorded this as paid.
                gateway_status = "success"
                paytabs_status = "A"

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
            # Never break the status endpoint because reconciliation failed,
            # but do not let the failure disappear either.
            logger.exception(
                "Reconciliation failed for subscription_id=%s", subscription_id
            )

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
