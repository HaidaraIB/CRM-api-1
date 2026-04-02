import json
import logging
import requests
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from crm_saas_api.responses import error_response, success_response, validation_error_response

from ..models import (
    Plan,
    Subscription,
    Payment,
    PaymentStatus,
    PaymentGateway,
    InvoiceStatus,
)
from ..serializers import CreateQicardPaymentSerializer
from ..qicard_utils import (
    verify_qicard_payment,
    create_qicard_payment_session,
    get_qicard_gateway,
)

logger = logging.getLogger(__name__)

@api_view(["POST"])
@permission_classes([AllowAny])
def create_qicard_payment(request):
    """
    Create a QiCard payment session for a subscription
    POST /api/payments/create-qicard-session/
    Body: { subscription_id: int, plan_id?: int, billing_cycle?: 'monthly' | 'yearly' }
    """
    # Validate serializer
    serializer = CreateQicardPaymentSerializer(data=request.data)
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

    # If plan_id is provided, update the subscription's plan (for plan changes)
    if plan_id:
        try:
            new_plan = Plan.objects.get(id=plan_id)
            subscription.plan = new_plan
            subscription.save(update_fields=['plan', 'updated_at'])
            logger.info(
                f"Updated subscription {subscription_id} plan to {new_plan.name} (ID: {plan_id})"
            )
        except Plan.DoesNotExist:
            return error_response(
                "Plan not found",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    # Allow renewals and plan changes even if subscription is active
    is_renewal = billing_cycle_param is not None
    if subscription.is_active and not plan_id and not is_renewal:
        return error_response(
            "Subscription is already active. Use renewal or plan change to proceed.",
            code="bad_request",
        )

    plan = subscription.plan

    # Determine billing cycle
    if billing_cycle_param:
        billing_cycle = billing_cycle_param
        logger.info(
            f"Using provided billing_cycle: {billing_cycle} for subscription {subscription_id}"
        )
    else:
        days_diff = (subscription.end_date - subscription.start_date).days
        billing_cycle = "yearly" if days_diff >= 330 else "monthly"
        logger.info(
            f"Calculated billing_cycle from subscription: {billing_cycle} "
            f"(days_diff={days_diff}) for subscription {subscription_id}"
        )
    
    # Get the correct amount based on billing cycle
    amount = float(
        plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly
    )
    
    logger.info(
        f"Creating QiCard payment for subscription {subscription_id}: "
        f"plan={plan.name}, billing_cycle={billing_cycle}, amount={amount}"
    )

    if amount <= 0:
        return error_response(
            "Plan is free, no payment required",
            code="bad_request",
        )

    # Prepare QiCard payment request
    user_email = subscription.company.owner.email
    user_name = f"{subscription.company.owner.first_name} {subscription.company.owner.last_name}".strip()
    if not user_name:
        user_name = subscription.company.owner.username

    # QiCard redirects to BACKEND return URL first, then we redirect to frontend
    return_url = f"{settings.API_BASE_URL}/api/payments/qicard-return/?subscription_id={subscription_id}"
    # Webhook URL for payment notifications
    notification_url = f"{settings.API_BASE_URL}/api/payments/qicard-webhook/"

    customer_phone = subscription.company.owner.phone or ""

    try:
        result = create_qicard_payment_session(
            amount=amount,
            customer_email=user_email,
            customer_name=user_name,
            customer_phone=customer_phone,
            subscription_id=subscription_id,
            return_url=return_url,
            notification_url=notification_url,
        )

        # Log the response for debugging
        logger.info(f"QiCard API response: {result}")
        
        # Extract payment ID and form URL from result
        payment_id = result.get("payment_id")
        form_url = result.get("form_url")
        request_id = result.get("request_id")
        
        if not payment_id or not form_url:
            logger.error(f"QiCard API did not return payment ID or form URL. Response: {result}")
            return error_response(
                "Failed to create payment session: No payment ID or form URL received",
                code="bad_request",
            )
        
        # Get QiCard gateway using the utility function
        from .qicard_utils import get_qicard_gateway
        qicard_gateway = get_qicard_gateway()

        if not qicard_gateway:
            logger.error("QiCard gateway not found after payment session creation")
            return error_response(
                "QiCard gateway is not configured or enabled",
                code="bad_request",
            )
        
        # Create payment record (QiCard plan amount in USD at creation; return may store IQD for display)
        from decimal import Decimal
        payment = Payment.objects.create(
            subscription=subscription,
            amount=amount,
            currency="USD",
            exchange_rate=Decimal("1"),
            amount_usd=Decimal(str(amount)),
            payment_method=qicard_gateway,
            payment_status=PaymentStatus.PENDING.value,
            tran_ref=payment_id,  # Store payment ID as tran_ref
        )

        logger.info(
            f"Created QiCard payment record: payment_id={payment.id}, "
            f"qicard_payment_id={payment_id}, subscription_id={subscription_id}, "
            f"form_url={form_url}"
        )

        # Return the form URL that frontend should redirect user to
        return success_response(
            data={
                "redirect_url": form_url,
                "payment_id": payment_id,
                "request_id": request_id,
            },
        )
    except requests.exceptions.HTTPError as e:
        error_details = {}
        try:
            error_details = e.response.json()
        except:
            error_details = {"message": e.response.text}

        return error_response(
            f"QiCard API error: {error_details.get('message', str(e))}",
            code="bad_request",
            details=error_details,
        )
    except ValueError as e:
        # Handle configuration errors (gateway not found, credentials missing, etc.)
        logger.error(f"QiCard configuration error: {str(e)}")
        return error_response(str(e), code="bad_request")
    except requests.exceptions.RequestException as e:
        logger.error(f"QiCard request error: {str(e)}", exc_info=True)
        return error_response(
            f"Error communicating with QiCard: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except Exception as e:
        return error_response(
            f"Unexpected error: {str(e)}",
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@csrf_exempt
@api_view(["POST", "GET"])
@permission_classes([AllowAny])
def qicard_return(request):
    """
    Handle QiCard return URL - QiCard redirects here after payment
    This endpoint verifies payment status and redirects to frontend success/failure page
    """
    logger = logging.getLogger(__name__)

    logger.info("=" * 80)
    logger.info("QICARD RETURN URL CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"GET params: {dict(request.GET)}")
    logger.info(f"POST data: {dict(request.POST) if hasattr(request, 'POST') else 'N/A'}")
    logger.info(f"Request data: {getattr(request, 'data', 'N/A')}")
    logger.info(f"Request body: {request.body.decode('utf-8') if request.body else 'Empty'}")
    logger.info("=" * 80)

    subscription_id = None
    payment_id = None
    status_param = None

    try:
        # QiCard redirects with paymentId and status in query parameters
        payment_id = request.GET.get("paymentId")
        status_param = request.GET.get("status")
        subscription_id_param = request.GET.get("subscription_id")

        if subscription_id_param:
            try:
                subscription_id = int(subscription_id_param)
            except (ValueError, TypeError):
                subscription_id = None

        if not payment_id:
            logger.error("Missing paymentId in QiCard return URL")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?status=failed&message=Missing payment ID"
            )

        # Verify payment status with QiCard API
        logger.info(f"Attempting to verify QiCard payment with paymentId: {payment_id}")
        from .qicard_utils import verify_qicard_payment, get_qicard_gateway
        verification_result = verify_qicard_payment(payment_id)
        logger.info(f"QiCard verification result: {json.dumps(verification_result, indent=2, default=str)}")

        qicard_payment_status = verification_result.get("status")
        logger.info(f"QiCard payment status from API: {qicard_payment_status}")

        if qicard_payment_status == "SUCCESS":
            logger.info("QiCard payment SUCCESS - Processing payment and updating subscription...")
            
            qicard_gateway = get_qicard_gateway()
            if not qicard_gateway:
                logger.error("QiCard gateway not found during return processing")
                frontend_url = settings.FRONTEND_URL
                return redirect(
                    f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=error&message=Payment gateway not configured"
                )

            # Note: verification_result.get("amount") returns amount in IQD
            # We should NOT update payment.amount with this value
            # The payment.amount was already set correctly (in USD) when payment was created
            
            # Find payment record - try with subscription_id first, then without
            payment = None
            if subscription_id:
                payment = Payment.objects.filter(
                    subscription__id=subscription_id,
                    payment_method=qicard_gateway,
                    tran_ref=payment_id,
                ).order_by("-created_at").first()
            
            # If not found, try without subscription_id filter
            if not payment:
                payment = Payment.objects.filter(
                    payment_method=qicard_gateway,
                    tran_ref=payment_id,
                ).order_by("-created_at").first()
                if payment:
                    subscription_id = payment.subscription.id
                    logger.info(f"Found payment without subscription_id filter, subscription_id={subscription_id}")

            if not payment:
                # If payment record not found, try to create one
                logger.warning(f"Payment record not found for QiCard paymentId {payment_id}. Attempting to create new record.")
                if not subscription_id:
                    # Try to get subscription_id from additionalInfo if not in URL
                    additional_info = verification_result.get("additionalInfo", {})
                    sub_id_from_info = additional_info.get("subscription_id")
                    if sub_id_from_info:
                        try:
                            subscription_id = int(sub_id_from_info)
                        except (ValueError, TypeError):
                            pass
                
                if subscription_id:
                    try:
                        subscription = Subscription.objects.get(id=subscription_id)
                        plan = subscription.plan
                        # Get original amount from plan (USD)
                        if plan:
                            # Determine billing cycle to get correct amount
                            days_diff = (subscription.end_date - subscription.start_date).days if subscription.end_date and subscription.start_date else 0
                            billing_cycle = "yearly" if days_diff >= 330 else "monthly"
                            original_amount = float(plan.price_yearly if billing_cycle == "yearly" else plan.price_monthly)
                        else:
                            # Fallback: convert IQD back to USD (not ideal but better than storing IQD)
                            amount_iqd = float(verification_result.get("amount", 0))
                            try:
                                from settings.models import SystemSettings
                                system_settings = SystemSettings.get_settings()
                                USD_TO_IQD_RATE = float(system_settings.usd_to_iqd_rate)
                                original_amount = amount_iqd / USD_TO_IQD_RATE
                            except:
                                original_amount = amount_iqd / 1300  # Default rate
                        
                        from decimal import Decimal
                        payment = Payment.objects.create(
                            subscription=subscription,
                            amount=original_amount,  # Store original amount in USD
                            currency="USD",
                            exchange_rate=Decimal("1"),
                            amount_usd=Decimal(str(original_amount)),
                            payment_method=qicard_gateway,
                            payment_status=PaymentStatus.COMPLETED.value,
                            tran_ref=payment_id,
                        )
                        logger.info(f"Created new payment record for QiCard paymentId {payment_id}, payment.id={payment.id}, amount={original_amount} USD")
                    except Subscription.DoesNotExist:
                        logger.error(f"Subscription {subscription_id} not found for QiCard paymentId {payment_id}")
                        frontend_url = settings.FRONTEND_URL
                        return redirect(
                            f"{frontend_url}/payment/success?status=failed&message=Subscription not found for payment"
                        )
                else:
                    logger.error(f"Could not determine subscription_id for QiCard paymentId {payment_id}")
                    frontend_url = settings.FRONTEND_URL
                    return redirect(
                        f"{frontend_url}/payment/success?status=failed&message=Could not link payment to subscription"
                    )
            else:
                # Update existing payment - keep original amount (USD), only update status
                logger.info(f"Updating existing payment: ID={payment.id}, Old status={payment.payment_status}, "
                           f"Amount={payment.amount} USD (keeping original amount, not updating with IQD value)")
                payment.payment_status = PaymentStatus.COMPLETED.value
                # DO NOT update payment.amount - it's already in USD, verification_result.amount is in IQD
                payment.save(update_fields=['payment_status', 'updated_at'])
                logger.info(f"Updated payment {payment.id} to COMPLETED (amount remains {payment.amount} USD).")
            
            # Update subscription end date and status
            # Use the same logic as Paytabs and Stripe for consistency
            subscription = payment.subscription
            plan = subscription.plan
            
            if plan:
                # Get payment amount (in USD) to determine billing cycle
                payment_amount = float(payment.amount)
                
                # Determine billing cycle based on payment amount (same as Paytabs/Stripe)
                billing_cycle = None
                if abs(payment_amount - float(plan.price_yearly)) < 0.01:
                    billing_cycle = "yearly"
                elif abs(payment_amount - float(plan.price_monthly)) < 0.01:
                    billing_cycle = "monthly"
                else:
                    # If amount doesn't match exactly, determine by comparing which is closer
                    yearly_diff = abs(payment_amount - float(plan.price_yearly))
                    monthly_diff = abs(payment_amount - float(plan.price_monthly))
                    billing_cycle = "yearly" if yearly_diff < monthly_diff else "monthly"
                    logger.warning(
                        f"Payment amount {payment_amount} doesn't exactly match plan prices "
                        f"(yearly: {plan.price_yearly}, monthly: {plan.price_monthly}). "
                        f"Using {billing_cycle} based on closest match."
                    )
                
                logger.info(f"Determined billing_cycle: {billing_cycle} for subscription {subscription.id} "
                           f"(payment_amount: {payment_amount}, plan.yearly: {plan.price_yearly}, plan.monthly: {plan.price_monthly})")
                
                # Calculate correct end_date based on billing cycle (same logic as Paytabs/Stripe)
                now = timezone.now()
                
                # Check if this is a new subscription (first payment) or renewal
                # Exclude the current payment we just created/updated
                payment_id_to_exclude = payment.id if payment else None
                has_completed_payments = Payment.objects.filter(
                    subscription=subscription,
                    payment_status=PaymentStatus.COMPLETED.value
                )
                if payment_id_to_exclude:
                    has_completed_payments = has_completed_payments.exclude(id=payment_id_to_exclude)
                has_completed_payments = has_completed_payments.exists()
                
                # Determine base_date based on subscription state (same as Paytabs/Stripe):
                # 1. Renewal: has completed payments AND end_date is in the future -> extend from end_date
                # 2. Reactivation: has completed payments BUT end_date is in the past -> start from now
                # 3. New subscription: no completed payments -> start from now or start_date
                if has_completed_payments and subscription.end_date > now:
                    # This is a renewal - extend from existing end_date
                    base_date = subscription.end_date
                    logger.info(
                        f"Subscription {subscription.id} is a RENEWAL - extending from end_date ({subscription.end_date.strftime('%Y-%m-%d %H:%M:%S')})"
                    )
                elif has_completed_payments and subscription.end_date <= now:
                    # This is a reactivation - subscription expired, start from now
                    base_date = now
                    logger.info(
                        f"Subscription {subscription.id} is a REACTIVATION (expired) - starting from now ({base_date.strftime('%Y-%m-%d %H:%M:%S')})"
                    )
                else:
                    # This is a new subscription (first payment) - start from now or start_date
                    if subscription.start_date and subscription.start_date > now:
                        base_date = subscription.start_date
                        logger.info(
                            f"Subscription {subscription.id} is NEW - using start_date ({subscription.start_date.strftime('%Y-%m-%d %H:%M:%S')})"
                        )
                    else:
                        base_date = now
                        logger.info(
                            f"Subscription {subscription.id} is NEW - using now ({base_date.strftime('%Y-%m-%d %H:%M:%S')})"
                        )
                
                # Calculate new end_date based on billing cycle
                if billing_cycle == "yearly":
                    new_end_date = base_date + timedelta(days=365)
                else:
                    new_end_date = base_date + timedelta(days=30)
                
                # Update subscription with correct end_date and activate it (same as Paytabs/Stripe)
                old_is_active = subscription.is_active
                old_end_date = subscription.end_date
                logger.info(f"BEFORE UPDATE - Subscription {subscription.id}: is_active={old_is_active}, "
                           f"end_date={old_end_date.strftime('%Y-%m-%d %H:%M:%S') if old_end_date else 'None'}")
                
                subscription.is_active = True
                subscription.end_date = new_end_date
                subscription.save(update_fields=['is_active', 'end_date', 'updated_at'])
                
                # Refresh from DB to verify update (same as Paytabs/Stripe)
                subscription.refresh_from_db()
                logger.info(f"AFTER UPDATE - Subscription {subscription.id}: is_active={subscription.is_active}, "
                           f"end_date={subscription.end_date.strftime('%Y-%m-%d %H:%M:%S') if subscription.end_date else 'None'}")
                
                logger.info(
                    f"✓ Activated subscription {subscription.id} for company {subscription.company.name if subscription.company else 'None'}. "
                    f"Billing cycle: {billing_cycle}, Amount: {payment_amount}, "
                    f"End date set to: {new_end_date.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                logger.warning(f"Subscription {subscription.id} has no plan, cannot set end date.")

            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription.id}&status=success"
            )
        elif qicard_payment_status == "FAILED" or qicard_payment_status == "AUTHENTICATION_FAILED":
            logger.warning(f"QiCard payment FAILED (status: {qicard_payment_status}). Redirecting to failed page.")
            
            # Update payment status if payment record exists
            qicard_gateway = get_qicard_gateway()
            if qicard_gateway and payment_id:
                payment = Payment.objects.filter(
                    tran_ref=payment_id,
                    payment_method=qicard_gateway,
                ).order_by("-created_at").first()
                if payment:
                    payment.payment_status = PaymentStatus.FAILED.value
                    payment.save(update_fields=['payment_status', 'updated_at'])
                    logger.info(f"Updated payment {payment.id} to FAILED.")
            
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=failed&message=Payment failed"
            )
        else:
            logger.warning(f"QiCard payment status is {qicard_payment_status}. Redirecting to pending/failed page.")
            frontend_url = settings.FRONTEND_URL
            return redirect(
                f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=pending&message=Payment status is {qicard_payment_status}"
            )

    except Subscription.DoesNotExist:
        logger.error(f"Subscription not found in QiCard return handler for subscription_id={subscription_id}")
        frontend_url = settings.FRONTEND_URL
        return redirect(
            f"{frontend_url}/payment/success?status=failed&message=Subscription not found"
        )
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"ERROR processing QiCard return: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback:", exc_info=True)
        logger.error("=" * 80)
        frontend_url = settings.FRONTEND_URL
        return redirect(
            f"{frontend_url}/payment/success?subscription_id={subscription_id or ''}&status=error&message={str(e)}"
        )


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def qicard_webhook(request):
    """
    Handle QiCard webhook notifications
    POST /api/payments/qicard-webhook/
    QiCard sends payment status updates via webhook
    """
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 80)
    logger.info("QICARD WEBHOOK CALLED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request body: {request.body.decode('utf-8') if request.body else 'Empty'}")
    logger.info("=" * 80)

    try:
        # Parse webhook payload
        if hasattr(request, 'data') and request.data:
            payload = request.data
        else:
            try:
                payload = json.loads(request.body.decode('utf-8'))
            except:
                payload = {}
        
        logger.info(f"QiCard webhook payload: {json.dumps(payload, indent=2, default=str)}")
        
        # Extract payment information from webhook
        payment_id = payload.get("paymentId")
        status = payload.get("status")
        request_id = payload.get("requestId")
        
        if not payment_id:
            logger.error("Missing paymentId in QiCard webhook")
            return error_response(
                "Missing paymentId",
                code="bad_request",
            )
        
        # Find payment by tran_ref (which stores payment_id)
        payment = Payment.objects.filter(tran_ref=payment_id).order_by("-created_at").first()
        
        if not payment:
            logger.warning(f"Payment not found for QiCard payment_id: {payment_id}")
            return error_response(
                "Payment not found",
                code="not_found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        
        subscription = payment.subscription
        
        logger.info(f"Found payment: ID={payment.id}, subscription_id={subscription.id}, "
                   f"status={status}, current_payment_status={payment.payment_status}")
        
        # Update payment status based on QiCard status
        if status == "SUCCESS":
            payment.payment_status = PaymentStatus.COMPLETED.value
            payment.save(update_fields=['payment_status', 'updated_at'])
            
            # Activate subscription
            if not subscription.is_active:
                subscription.is_active = True
                subscription.start_date = timezone.now()
                
                # Calculate end date based on billing cycle
                days_diff = (subscription.end_date - subscription.start_date).days if subscription.end_date else 0
                billing_cycle = "yearly" if days_diff >= 330 else "monthly"
                
                if billing_cycle == "yearly":
                    subscription.end_date = subscription.start_date + timedelta(days=365)
                else:
                    subscription.end_date = subscription.start_date + timedelta(days=30)
                
                subscription.save(update_fields=['is_active', 'start_date', 'end_date', 'updated_at'])
                logger.info(f"Activated subscription {subscription.id}, end_date={subscription.end_date}")
            
            logger.info(f"Payment {payment.id} marked as COMPLETED")
        elif status == "FAILED" or status == "AUTHENTICATION_FAILED":
            payment.payment_status = PaymentStatus.FAILED.value
            payment.save(update_fields=['payment_status', 'updated_at'])
            logger.info(f"Payment {payment.id} marked as FAILED")
        else:
            # CREATED or other status - keep as PENDING
            logger.info(f"Payment {payment.id} status is {status}, keeping as PENDING")
        
        return success_response(message="OK")
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"ERROR processing QiCard webhook: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Traceback:", exc_info=True)
        logger.error("=" * 80)
        return error_response(
            str(e),
            code="server_error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
