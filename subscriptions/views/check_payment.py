import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from crm_saas_api.responses import error_response, success_response

from ..models import Subscription, Payment, PaymentStatus
from ..paytabs_utils import verify_paytabs_payment
from ..stripe_utils import verify_stripe_payment
from ..zaincash_utils import check_zaincash_payment_status
from django.utils import timezone

logger = logging.getLogger(__name__)

@api_view(["GET"])
@permission_classes([AllowAny])
def check_payment_status(request, subscription_id):
    """
    Check payment status by subscription_id - for frontend polling
    GET /api/payments/subscription/{subscription_id}/status/
    Returns payment status and subscription status
    """
    logger = logging.getLogger(__name__)
    try:
        subscription = Subscription.objects.get(id=subscription_id)
        logger.info(
            f"Checking payment status for subscription {subscription_id}, is_active: {subscription.is_active}"
        )

        # Get payment - handle case where payment might not exist yet
        # Find payment by subscription (regardless of gateway)
        try:
            payment = (
                Payment.objects.filter(subscription=subscription)
                .order_by("-created_at")
                .first()
            )
        except Exception as e:
            logger.warning(f"Error fetching payment: {str(e)}")
            payment = None

        # Refresh subscription from DB to get latest is_active status
        subscription.refresh_from_db()

        # Get payment status from DB
        payment_status_value = payment.payment_status if payment else "pending"
        paytabs_status = None
        gateway_status = None

        # PRIORITY: If subscription is active, payment MUST be completed
        # This is the source of truth - if subscription is active, payment is done
        if subscription.is_active:
            payment_status_value = PaymentStatus.COMPLETED.value
            paytabs_status = "A"  # For PayTabs compatibility
            gateway_status = "success"  # Generic success status
            logger.info(
                f"Subscription {subscription_id} is ACTIVE - returning completed status immediately"
            )
        elif payment and payment.tran_ref:
            # Check payment gateway type and verify accordingly
            payment_gateway = payment.payment_method
            if payment_gateway:
                gateway_name = payment_gateway.name.lower()
                
                # If it's a Zain Cash payment, check status using transaction ID
                if "zain" in gateway_name or "zaincash" in gateway_name:
                    try:
                        from .zaincash_utils import check_zaincash_payment_status
                        result = check_zaincash_payment_status(payment.tran_ref)
                        gateway_status = result.get("status", "pending")
                        if gateway_status == "success":
                            payment_status_value = PaymentStatus.COMPLETED.value
                            # Also update payment record if status changed
                            if payment.payment_status != PaymentStatus.COMPLETED.value:
                                payment.payment_status = PaymentStatus.COMPLETED.value
                                payment.save()
                                # Activate subscription if payment is successful
                                if not subscription.is_active:
                                    subscription.is_active = True
                                    subscription.start_date = timezone.now()
                                    subscription.save()
                    except Exception as e:
                        logger.warning(f"Could not verify Zain Cash payment: {str(e)}")
                        # If payment is completed in DB but verification fails, assume approved
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"
                
                # If it's a PayTabs payment, verify with PayTabs
                elif "paytabs" in gateway_name:
                    try:
                        result = verify_paytabs_payment(payment.tran_ref)
                        paytabs_status = result.get("payment_result", {}).get(
                            "response_status"
                        )
                    except Exception as e:
                        logger.warning(f"Could not verify payment with PayTabs: {str(e)}")
                        # If payment is completed in DB but verification fails, assume approved
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            paytabs_status = "A"
                
                # If it's a Stripe payment, verify with Stripe
                elif "stripe" in gateway_name:
                    try:
                        # For Stripe, tran_ref is the session_id
                        result = verify_stripe_payment(payment.tran_ref)
                        stripe_status = result.get("stripe_payment_status")
                        if stripe_status == "paid":
                            gateway_status = "success"
                            payment_status_value = PaymentStatus.COMPLETED.value
                            # Also update payment record if status changed
                            if payment.payment_status != PaymentStatus.COMPLETED.value:
                                payment.payment_status = PaymentStatus.COMPLETED.value
                                payment.save()
                                # Activate subscription if payment is successful
                                if not subscription.is_active:
                                    subscription.is_active = True
                                    subscription.start_date = timezone.now()
                                    subscription.save()
                    except Exception as e:
                        logger.warning(f"Could not verify Stripe payment: {str(e)}")
                        # If payment is completed in DB but verification fails, assume approved
                        if payment.payment_status == PaymentStatus.COMPLETED.value:
                            gateway_status = "success"

            # If payment is completed in DB, ensure status is set
            if (
                payment.payment_status == PaymentStatus.COMPLETED.value
                and not paytabs_status
                and not gateway_status
            ):
                paytabs_status = "A"  # For PayTabs compatibility
                gateway_status = "success"  # Generic success

        # Return all fields the frontend needs - ensure values match what frontend expects
        # Check if subscription is truly active (considering end_date)
        # Normalize free/trial subscriptions: they do not have billing cycles.
        # Older records may have been created with a default 30-day end_date; fix/override using plan.trial_days.
        try:
            plan = subscription.plan
            is_free_or_trial = float(plan.price_monthly) <= 0 and float(plan.price_yearly) <= 0
            has_completed_payment = (
                Payment.objects.filter(
                    subscription=subscription,
                    payment_status=PaymentStatus.COMPLETED.value,
                ).exists()
            )
            if is_free_or_trial and not has_completed_payment:
                from datetime import timedelta
                if int(getattr(plan, "trial_days", 0) or 0) > 0:
                    computed_end = subscription.start_date + timedelta(days=int(plan.trial_days))
                else:
                    computed_end = subscription.start_date + timedelta(days=365 * 100)
                # If stored end_date is clearly not matching computed_end, update it.
                if subscription.end_date is None or abs((subscription.end_date - computed_end).days) >= 1:
                    subscription.end_date = computed_end
                    subscription.save(update_fields=["end_date", "updated_at"])
                    subscription.refresh_from_db()
        except Exception as _exc:
            # Never break status endpoint due to normalization
            pass

        is_truly_active = subscription.is_truly_active()
        days_until_expiry = subscription.days_until_expiry()
        is_expiring_soon = subscription.is_expiring_soon(days_threshold=30)
        
        # If subscription is not truly active but is_active=True, update it
        if subscription.is_active and not is_truly_active:
            subscription.is_active = False
            subscription.save(update_fields=['is_active', 'updated_at'])
            subscription.refresh_from_db()
            is_truly_active = False
            logger.info(f"Subscription {subscription_id} was marked as inactive due to expired end_date")

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
        logger = logging.getLogger(__name__)
        logger.error(f"Error checking payment status: {str(e)}", exc_info=True)
        return error_response(
            f"Error checking payment status: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
